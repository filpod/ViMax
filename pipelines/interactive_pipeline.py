"""
InteractivePipeline: wraps Idea2VideoPipeline phase-by-phase with WebSocket broadcast
and per-phase user approval/regenerate/save decisions.
"""

import asyncio
import builtins
import glob
import json
import os
from typing import Any, Callable, Dict, List, Optional

from interfaces import CharacterInScene
from pipelines.idea2video_pipeline import Idea2VideoPipeline
from pipelines.script2video_pipeline import Script2VideoPipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PHASE_LABELS: Dict[str, str] = {
    "story": "Story",
    "characters": "Characters",
    "portraits": "Character Portraits",
    "script": "Script",
}


def _scene_label(phase_base: str, scene_idx: int) -> str:
    labels = {
        "storyboard": f"Scene {scene_idx} Storyboard",
        "shots": f"Scene {scene_idx} Shot Descriptions",
        "frames": f"Scene {scene_idx} Frames",
        "clips": f"Scene {scene_idx} Clips",
    }
    return labels.get(phase_base, f"{phase_base}_{scene_idx}")


# ---------------------------------------------------------------------------
# InteractivePipeline
# ---------------------------------------------------------------------------

class InteractivePipeline:
    """
    Runs Idea2VideoPipeline phase-by-phase with interactive approval.

    Usage::

        pipeline = InteractivePipeline(config_path="configs/idea2video.yaml")
        pipeline.set_broadcast(broadcast_fn)
        await pipeline.run(idea, user_requirement, style)
    """

    def __init__(self, config_path: str):
        self.config_path = config_path

        # These are initialised in run() after we know the working_dir
        self._idea2video: Optional[Idea2VideoPipeline] = None

        # WebSocket broadcast callback (set externally)
        self._broadcast: Optional[Callable] = None

        # Decision synchronisation
        self._decision_event: asyncio.Event = asyncio.Event()
        self._pending_decision: Optional[Dict[str, Any]] = None

        # Log queue – server drains this and broadcasts log messages
        self.log_queue: asyncio.Queue = asyncio.Queue()

        # Attempt counter per phase
        self._attempts: Dict[str, int] = {}

        # Accumulated user feedback per phase (for prompt injection)
        self._feedback_history: Dict[str, List[str]] = {}

        # The mutable user_requirement that grows with revisions
        self._user_requirement: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_broadcast(self, fn: Callable):
        """Wire up the WebSocket broadcast function."""
        self._broadcast = fn

    async def submit_decision(
        self,
        action: str,
        feedback: str = "",
        content: str = "",
    ):
        """Called by the server when the user clicks Approve / Regen / Save."""
        self._pending_decision = {
            "action": action,
            "feedback": feedback,
            "content": content,
        }
        self._decision_event.set()

    async def run(self, idea: str, user_requirement: str, style: str):
        """Entry point – runs the full interactive pipeline."""
        self._user_requirement = user_requirement
        self._idea2video = Idea2VideoPipeline.init_from_config(self.config_path)
        p = self._idea2video  # shorthand

        try:
            # ----------------------------------------------------------
            # Phase 1: Story
            # ----------------------------------------------------------
            story = await self._run_phase(
                phase="story",
                label="Story",
                cache_files=[os.path.join(p.working_dir, "story.txt")],
                runner=lambda: p.develop_story(idea=idea, user_requirement=self._user_requirement),
                output_builder=lambda result: {"type": "text", "content": result},
                cache_saver=lambda content, path: open(path, "w", encoding="utf-8").write(content),
                save_path=os.path.join(p.working_dir, "story.txt"),
            )

            # ----------------------------------------------------------
            # Phase 2: Characters
            # ----------------------------------------------------------
            characters_raw = await self._run_phase(
                phase="characters",
                label="Characters",
                cache_files=[os.path.join(p.working_dir, "characters.json")],
                runner=lambda: p.extract_characters(story=story),
                output_builder=lambda result: {
                    "type": "json",
                    "content": [c.model_dump() for c in result],
                },
                cache_saver=lambda content, path: open(path, "w", encoding="utf-8").write(
                    json.dumps(content, ensure_ascii=False, indent=4)
                ),
                save_path=os.path.join(p.working_dir, "characters.json"),
                result_transformer=lambda result: [c.model_dump() for c in result],
                result_loader=lambda path: [
                    CharacterInScene.model_validate(c)
                    for c in json.load(open(path, encoding="utf-8"))
                ],
            )
            # Normalise to list of CharacterInScene
            if isinstance(characters_raw, list) and characters_raw and isinstance(characters_raw[0], dict):
                characters = [CharacterInScene.model_validate(c) for c in characters_raw]
            else:
                characters = characters_raw

            # ----------------------------------------------------------
            # Phase 3: Portraits
            # ----------------------------------------------------------
            portraits_registry = await self._run_portraits_phase(
                p=p,
                characters=characters,
                style=style,
            )

            # ----------------------------------------------------------
            # Phase 4: Script
            # ----------------------------------------------------------
            scene_scripts = await self._run_phase(
                phase="script",
                label="Script",
                cache_files=[os.path.join(p.working_dir, "script.json")],
                runner=lambda: p.write_script_based_on_story(
                    story=story, user_requirement=self._user_requirement
                ),
                output_builder=lambda result: {"type": "json", "content": result},
                cache_saver=lambda content, path: open(path, "w", encoding="utf-8").write(
                    json.dumps(content, ensure_ascii=False, indent=4)
                ),
                save_path=os.path.join(p.working_dir, "script.json"),
                result_loader=lambda path: json.load(open(path, encoding="utf-8")),
            )

            # ----------------------------------------------------------
            # Per-scene phases
            # ----------------------------------------------------------
            all_video_paths: List[str] = []

            for scene_idx, scene_script in enumerate(scene_scripts):
                scene_working_dir = os.path.join(p.working_dir, f"scene_{scene_idx}")
                os.makedirs(scene_working_dir, exist_ok=True)

                s2v = Script2VideoPipeline(
                    chat_model=p.chat_model,
                    image_generator=p.image_generator,
                    video_generator=p.video_generator,
                    working_dir=scene_working_dir,
                )

                # Phase 5: Storyboard
                storyboard = await self._run_storyboard_phase(
                    s2v=s2v,
                    scene_idx=scene_idx,
                    scene_script=scene_script,
                    characters=characters,
                )

                # Phase 6: Shot Descriptions
                shot_descriptions = await self._run_shot_descriptions_phase(
                    s2v=s2v,
                    scene_idx=scene_idx,
                    storyboard=storyboard,
                    characters=characters,
                )

                # Phase 7: Frames
                await self._run_frames_phase(
                    s2v=s2v,
                    scene_idx=scene_idx,
                    shot_descriptions=shot_descriptions,
                    characters=characters,
                    portraits_registry=portraits_registry,
                )

                # Phase 8: Clips
                scene_video_path = await self._run_clips_phase(
                    s2v=s2v,
                    scene_idx=scene_idx,
                    shot_descriptions=shot_descriptions,
                )
                all_video_paths.append(scene_video_path)

            # ----------------------------------------------------------
            # Concatenate final video
            # ----------------------------------------------------------
            from moviepy import VideoFileClip, concatenate_videoclips

            final_video_path = os.path.join(p.working_dir, "final_video.mp4")
            if not os.path.exists(final_video_path):
                await self._log("Concatenating all scene videos...")
                video_clips = [VideoFileClip(vp) for vp in all_video_paths]
                final_video = concatenate_videoclips(video_clips)
                final_video.write_videofile(final_video_path, codec="libx264", preset="medium")

            await self._broadcast_msg({"type": "pipeline_complete", "video_path": os.path.abspath(final_video_path)})

        except Exception as exc:
            import traceback
            err = traceback.format_exc()
            await self._log(f"ERROR: {err}")
            await self._broadcast_msg({"type": "pipeline_error", "error": str(exc)})
            raise

    # ------------------------------------------------------------------
    # Generic phase runner
    # ------------------------------------------------------------------

    async def _run_phase(
        self,
        phase: str,
        label: str,
        cache_files: List[str],
        runner: Callable,
        output_builder: Callable,
        cache_saver: Callable,
        save_path: str,
        result_transformer: Optional[Callable] = None,
        result_loader: Optional[Callable] = None,
    ):
        """
        Generic phase runner with approval loop.

        Returns the raw result from runner() (or loaded from cache after save/approve).
        """
        attempt = self._attempts.get(phase, 1)
        self._attempts[phase] = attempt

        while True:
            await self._broadcast_msg({"type": "phase_start", "phase": phase, "label": label})
            await self._log(f"Starting phase: {label} (attempt {attempt})")

            # Run with print capture
            result = await self._run_with_log_capture(runner)

            # Build output payload
            raw_for_output = result_transformer(result) if result_transformer else result
            output = output_builder(raw_for_output)

            await self._broadcast_msg({
                "type": "phase_complete",
                "phase": phase,
                "label": label,
                "output": output,
            })

            # Wait for user decision
            decision = await self._wait_for_decision()
            action = decision["action"]
            feedback = decision.get("feedback", "")
            content = decision.get("content", "")

            if action == "approve":
                await self._broadcast_msg({"type": "phase_approved", "phase": phase})
                return result

            elif action == "save":
                # User edited the content – persist it and continue
                await self._persist_save(phase=phase, cache_files=cache_files,
                                          save_path=save_path, content=content,
                                          cache_saver=cache_saver)
                await self._broadcast_msg({"type": "phase_approved", "phase": phase})
                # Reload from the saved file if loader provided
                if result_loader:
                    return result_loader(save_path)
                return result

            elif action == "regenerate":
                # Delete cache files, accumulate feedback, loop
                attempt += 1
                self._attempts[phase] = attempt
                self._accumulate_feedback(phase, feedback)
                await self._delete_cache_files(phase, cache_files)
                await self._broadcast_msg({
                    "type": "phase_regen",
                    "phase": phase,
                    "attempt": attempt,
                })
                # Update user_requirement with accumulated feedback
                self._inject_feedback_into_requirement(phase)

    # ------------------------------------------------------------------
    # Portraits phase (special: multiple cache files + complex output)
    # ------------------------------------------------------------------

    async def _run_portraits_phase(
        self,
        p: Idea2VideoPipeline,
        characters: List[CharacterInScene],
        style: str,
    ) -> Dict:
        phase = "portraits"
        label = "Character Portraits"
        registry_path = os.path.join(p.working_dir, "character_portraits_registry.json")
        attempt = self._attempts.get(phase, 1)
        self._attempts[phase] = attempt

        while True:
            await self._broadcast_msg({"type": "phase_start", "phase": phase, "label": label})
            await self._log(f"Starting phase: {label} (attempt {attempt})")

            portraits_registry = await self._run_with_log_capture(
                lambda: p.generate_character_portraits(
                    characters=characters,
                    character_portraits_registry=None,
                    style=style,
                )
            )

            # Build portraits output
            portraits_list = []
            for char_name, views_dict in portraits_registry.items():
                entry = {
                    "name": char_name,
                    "views": {
                        view: view_data["path"]
                        for view, view_data in views_dict.items()
                    },
                }
                portraits_list.append(entry)

            output = {"type": "portraits", "content": portraits_list}

            await self._broadcast_msg({
                "type": "phase_complete",
                "phase": phase,
                "label": label,
                "output": output,
            })

            decision = await self._wait_for_decision()
            action = decision["action"]
            feedback = decision.get("feedback", "")

            if action == "approve":
                await self._broadcast_msg({"type": "phase_approved", "phase": phase})
                return portraits_registry

            elif action == "save":
                # For portraits we don't support inline editing; treat as approve
                await self._broadcast_msg({"type": "phase_approved", "phase": phase})
                return portraits_registry

            elif action == "regenerate":
                attempt += 1
                self._attempts[phase] = attempt
                self._accumulate_feedback(phase, feedback)
                # Delete registry + all portrait images
                cache_files = self._collect_portrait_cache_files(p.working_dir)
                await self._delete_cache_files(phase, cache_files)
                await self._broadcast_msg({
                    "type": "phase_regen",
                    "phase": phase,
                    "attempt": attempt,
                })
                self._inject_feedback_into_requirement(phase)

    def _collect_portrait_cache_files(self, working_dir: str) -> List[str]:
        files = []
        registry_path = os.path.join(working_dir, "character_portraits_registry.json")
        if os.path.exists(registry_path):
            files.append(registry_path)
        portraits_dir = os.path.join(working_dir, "character_portraits")
        if os.path.isdir(portraits_dir):
            files.append(portraits_dir)
        return files

    # ------------------------------------------------------------------
    # Storyboard phase
    # ------------------------------------------------------------------

    async def _run_storyboard_phase(
        self,
        s2v: Script2VideoPipeline,
        scene_idx: int,
        scene_script,
        characters: List[CharacterInScene],
    ):
        phase = f"storyboard_{scene_idx}"
        label = _scene_label("storyboard", scene_idx)
        storyboard_path = os.path.join(s2v.working_dir, "storyboard.json")
        attempt = self._attempts.get(phase, 1)
        self._attempts[phase] = attempt

        while True:
            await self._broadcast_msg({"type": "phase_start", "phase": phase, "label": label})
            await self._log(f"Starting phase: {label} (attempt {attempt})")

            storyboard = await self._run_with_log_capture(
                lambda: s2v.design_storyboard(
                    script=scene_script,
                    characters=characters,
                    user_requirement=self._user_requirement,
                )
            )

            output = {
                "type": "json",
                "content": [shot.model_dump() for shot in storyboard],
            }

            await self._broadcast_msg({
                "type": "phase_complete",
                "phase": phase,
                "label": label,
                "output": output,
            })

            decision = await self._wait_for_decision()
            action = decision["action"]
            feedback = decision.get("feedback", "")
            content = decision.get("content", "")

            if action == "approve":
                await self._broadcast_msg({"type": "phase_approved", "phase": phase})
                return storyboard

            elif action == "save":
                # Persist user-edited JSON
                try:
                    parsed = json.loads(content)
                except Exception:
                    parsed = json.loads(content) if content else []
                with open(storyboard_path, "w", encoding="utf-8") as f:
                    json.dump(parsed, f, ensure_ascii=False, indent=4)
                # Reload
                from interfaces import ShotBriefDescription
                storyboard = [ShotBriefDescription.model_validate(s) for s in parsed]
                # Re-init shot_desc_events
                for sbd in storyboard:
                    s2v.shot_desc_events[sbd.idx] = asyncio.Event()
                await self._broadcast_msg({"type": "phase_approved", "phase": phase})
                return storyboard

            elif action == "regenerate":
                attempt += 1
                self._attempts[phase] = attempt
                self._accumulate_feedback(phase, feedback)
                await self._delete_cache_files(phase, [storyboard_path])
                # Also clear shot_desc_events
                s2v.shot_desc_events = {}
                await self._broadcast_msg({
                    "type": "phase_regen",
                    "phase": phase,
                    "attempt": attempt,
                })
                self._inject_feedback_into_requirement(phase)

    # ------------------------------------------------------------------
    # Shot descriptions phase
    # ------------------------------------------------------------------

    async def _run_shot_descriptions_phase(
        self,
        s2v: Script2VideoPipeline,
        scene_idx: int,
        storyboard,
        characters: List[CharacterInScene],
    ):
        phase = f"shots_{scene_idx}"
        label = _scene_label("shots", scene_idx)
        shots_dir = os.path.join(s2v.working_dir, "shots")
        attempt = self._attempts.get(phase, 1)
        self._attempts[phase] = attempt

        while True:
            await self._broadcast_msg({"type": "phase_start", "phase": phase, "label": label})
            await self._log(f"Starting phase: {label} (attempt {attempt})")

            shot_descriptions = await self._run_with_log_capture(
                lambda: s2v.decompose_visual_descriptions(
                    shot_brief_descriptions=storyboard,
                    characters=characters,
                )
            )

            output = {
                "type": "json",
                "content": [sd.model_dump() for sd in shot_descriptions],
            }

            await self._broadcast_msg({
                "type": "phase_complete",
                "phase": phase,
                "label": label,
                "output": output,
            })

            decision = await self._wait_for_decision()
            action = decision["action"]
            feedback = decision.get("feedback", "")
            content = decision.get("content", "")

            if action == "approve":
                await self._broadcast_msg({"type": "phase_approved", "phase": phase})
                return shot_descriptions

            elif action == "save":
                from interfaces import ShotDescription
                try:
                    parsed = json.loads(content)
                except Exception:
                    parsed = []
                shot_descriptions = []
                for item in parsed:
                    sd = ShotDescription.model_validate(item)
                    sd_path = os.path.join(shots_dir, f"{sd.idx}", "shot_description.json")
                    os.makedirs(os.path.dirname(sd_path), exist_ok=True)
                    with open(sd_path, "w", encoding="utf-8") as f:
                        json.dump(sd.model_dump(), f, ensure_ascii=False, indent=4)
                    # Re-init frame_events for each shot
                    if sd.variation_type in ["medium", "large"]:
                        s2v.frame_events[sd.idx] = {
                            "first_frame": asyncio.Event(),
                            "last_frame": asyncio.Event(),
                        }
                    else:
                        s2v.frame_events[sd.idx] = {
                            "first_frame": asyncio.Event(),
                        }
                    shot_descriptions.append(sd)
                await self._broadcast_msg({"type": "phase_approved", "phase": phase})
                return shot_descriptions

            elif action == "regenerate":
                attempt += 1
                self._attempts[phase] = attempt
                self._accumulate_feedback(phase, feedback)
                # Delete all shot_description.json files
                shot_desc_files = glob.glob(
                    os.path.join(shots_dir, "*", "shot_description.json")
                )
                await self._delete_cache_files(phase, shot_desc_files)
                # Reset frame_events
                s2v.frame_events = {}
                await self._broadcast_msg({
                    "type": "phase_regen",
                    "phase": phase,
                    "attempt": attempt,
                })
                self._inject_feedback_into_requirement(phase)

    # ------------------------------------------------------------------
    # Frames phase
    # ------------------------------------------------------------------

    async def _run_frames_phase(
        self,
        s2v: Script2VideoPipeline,
        scene_idx: int,
        shot_descriptions,
        characters: List[CharacterInScene],
        portraits_registry: Dict,
    ):
        phase = f"frames_{scene_idx}"
        label = _scene_label("frames", scene_idx)
        shots_dir = os.path.join(s2v.working_dir, "shots")
        attempt = self._attempts.get(phase, 1)
        self._attempts[phase] = attempt

        while True:
            await self._broadcast_msg({"type": "phase_start", "phase": phase, "label": label})
            await self._log(f"Starting phase: {label} (attempt {attempt})")

            # Build camera tree then generate frames
            camera_tree = await self._run_with_log_capture(
                lambda: s2v.construct_camera_tree(shot_descriptions=shot_descriptions)
            )

            priority_shot_idxs = [
                camera.parent_cam_idx
                for camera in camera_tree
                if camera.parent_cam_idx is not None
            ]

            frame_tasks = [
                s2v.generate_frames_for_single_camera(
                    camera=camera,
                    shot_descriptions=shot_descriptions,
                    characters=characters,
                    character_portraits_registry=portraits_registry,
                    priority_shot_idxs=priority_shot_idxs,
                )
                for camera in camera_tree
            ]
            await self._run_with_log_capture(lambda: asyncio.gather(*frame_tasks))

            # Collect frame paths for output
            frames_list = []
            for sd in shot_descriptions:
                ff_path = os.path.join(shots_dir, f"{sd.idx}", "first_frame.png")
                if os.path.exists(ff_path):
                    frames_list.append({
                        "shot_idx": sd.idx,
                        "path": os.path.abspath(ff_path),
                        "frame_type": "first_frame",
                    })
                lf_path = os.path.join(shots_dir, f"{sd.idx}", "last_frame.png")
                if os.path.exists(lf_path):
                    frames_list.append({
                        "shot_idx": sd.idx,
                        "path": os.path.abspath(lf_path),
                        "frame_type": "last_frame",
                    })

            output = {"type": "images", "content": frames_list}

            await self._broadcast_msg({
                "type": "phase_complete",
                "phase": phase,
                "label": label,
                "output": output,
            })

            decision = await self._wait_for_decision()
            action = decision["action"]
            feedback = decision.get("feedback", "")

            if action in ("approve", "save"):
                await self._broadcast_msg({"type": "phase_approved", "phase": phase})
                return

            elif action == "regenerate":
                attempt += 1
                self._attempts[phase] = attempt
                self._accumulate_feedback(phase, feedback)
                # Delete all frame PNGs and camera tree
                frame_files = (
                    glob.glob(os.path.join(shots_dir, "*", "first_frame.png"))
                    + glob.glob(os.path.join(shots_dir, "*", "last_frame.png"))
                    + glob.glob(os.path.join(shots_dir, "*", "first_frame_selector_output.json"))
                    + glob.glob(os.path.join(shots_dir, "*", "last_frame_selector_output.json"))
                    + glob.glob(os.path.join(shots_dir, "*", "new_camera_*.png"))
                    + glob.glob(os.path.join(shots_dir, "*", "transition_video_*.mp4"))
                    + [os.path.join(s2v.working_dir, "camera_tree.json")]
                )
                await self._delete_cache_files(phase, frame_files)
                # Reset frame_events
                for sd in shot_descriptions:
                    if sd.variation_type in ["medium", "large"]:
                        s2v.frame_events[sd.idx] = {
                            "first_frame": asyncio.Event(),
                            "last_frame": asyncio.Event(),
                        }
                    else:
                        s2v.frame_events[sd.idx] = {
                            "first_frame": asyncio.Event(),
                        }
                await self._broadcast_msg({
                    "type": "phase_regen",
                    "phase": phase,
                    "attempt": attempt,
                })
                self._inject_feedback_into_requirement(phase)

    # ------------------------------------------------------------------
    # Clips phase
    # ------------------------------------------------------------------

    async def _run_clips_phase(
        self,
        s2v: Script2VideoPipeline,
        scene_idx: int,
        shot_descriptions,
    ) -> str:
        phase = f"clips_{scene_idx}"
        label = _scene_label("clips", scene_idx)
        shots_dir = os.path.join(s2v.working_dir, "shots")
        attempt = self._attempts.get(phase, 1)
        self._attempts[phase] = attempt

        while True:
            await self._broadcast_msg({"type": "phase_start", "phase": phase, "label": label})
            await self._log(f"Starting phase: {label} (attempt {attempt})")

            video_tasks = [
                s2v.generate_video_for_single_shot(shot_description=sd)
                for sd in shot_descriptions
            ]
            await self._run_with_log_capture(lambda: asyncio.gather(*video_tasks))

            # Concatenate scene video
            from moviepy import VideoFileClip, concatenate_videoclips

            scene_video_path = os.path.join(s2v.working_dir, "final_video.mp4")
            if not os.path.exists(scene_video_path):
                await self._log(f"Concatenating clips for scene {scene_idx}...")
                clips = [
                    VideoFileClip(os.path.join(shots_dir, f"{sd.idx}", "video.mp4"))
                    for sd in shot_descriptions
                ]
                final_clip = concatenate_videoclips(clips)
                final_clip.write_videofile(scene_video_path, codec="libx264", preset="medium")

            # Collect clip paths
            clips_list = []
            for sd in shot_descriptions:
                vp = os.path.join(shots_dir, f"{sd.idx}", "video.mp4")
                if os.path.exists(vp):
                    clips_list.append({
                        "shot_idx": sd.idx,
                        "path": os.path.abspath(vp),
                    })

            output = {"type": "videos", "content": clips_list}

            await self._broadcast_msg({
                "type": "phase_complete",
                "phase": phase,
                "label": label,
                "output": output,
            })

            decision = await self._wait_for_decision()
            action = decision["action"]
            feedback = decision.get("feedback", "")

            if action in ("approve", "save"):
                await self._broadcast_msg({"type": "phase_approved", "phase": phase})
                return scene_video_path

            elif action == "regenerate":
                attempt += 1
                self._attempts[phase] = attempt
                self._accumulate_feedback(phase, feedback)
                # Delete video files and scene final
                video_files = (
                    glob.glob(os.path.join(shots_dir, "*", "video.mp4"))
                    + [scene_video_path]
                )
                await self._delete_cache_files(phase, video_files)
                await self._broadcast_msg({
                    "type": "phase_regen",
                    "phase": phase,
                    "attempt": attempt,
                })
                self._inject_feedback_into_requirement(phase)

    # ------------------------------------------------------------------
    # Decision helpers
    # ------------------------------------------------------------------

    async def _wait_for_decision(self) -> Dict[str, Any]:
        """Block until submit_decision() is called, then return the decision dict."""
        self._decision_event.clear()
        self._pending_decision = None
        await self._decision_event.wait()
        decision = self._pending_decision
        self._pending_decision = None
        return decision

    # ------------------------------------------------------------------
    # Feedback helpers
    # ------------------------------------------------------------------

    def _accumulate_feedback(self, phase: str, feedback: str):
        if not feedback:
            return
        if phase not in self._feedback_history:
            self._feedback_history[phase] = []
        self._feedback_history[phase].append(feedback)

    def _inject_feedback_into_requirement(self, phase: str):
        """Append all accumulated feedback for this phase to user_requirement."""
        history = self._feedback_history.get(phase, [])
        if not history:
            return
        # Only append the latest feedback (already accumulated)
        latest = history[-1]
        revision_n = len(history)
        annotation = f"\n[Revision {revision_n}]: {latest}"
        if annotation not in self._user_requirement:
            self._user_requirement += annotation

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    async def _delete_cache_files(self, phase: str, files: List[str]):
        import shutil
        for path in files:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
                await self._log(f"Deleted directory: {path}")
            elif os.path.exists(path):
                os.remove(path)
                await self._log(f"Deleted cache file: {path}")

    async def _persist_save(
        self,
        phase: str,
        cache_files: List[str],
        save_path: str,
        content: str,
        cache_saver: Callable,
    ):
        """Persist user-edited content to the cache file."""
        # For text phases content is a string; for JSON phases it may be a JSON string
        if save_path.endswith(".json"):
            try:
                parsed = json.loads(content)
                cache_saver(parsed, save_path)
            except Exception:
                cache_saver(content, save_path)
        else:
            cache_saver(content, save_path)
        await self._log(f"Saved user-edited content to {save_path}")

    # ------------------------------------------------------------------
    # Logging / broadcast helpers
    # ------------------------------------------------------------------

    async def _log(self, message: str):
        await self.log_queue.put(message)

    async def _broadcast_msg(self, msg: Dict[str, Any]):
        if self._broadcast:
            await self._broadcast(msg)

    async def _run_with_log_capture(self, coro_fn: Callable):
        """
        Run an async callable while intercepting builtins.print so that all
        pipeline print() calls are also pushed to self.log_queue.
        """
        original_print = builtins.print
        queue = self.log_queue

        def capturing_print(*args, **kwargs):
            # Call the original print for console visibility
            original_print(*args, **kwargs)
            # Also enqueue the message for WebSocket broadcast
            msg = " ".join(str(a) for a in args)
            # We can't await inside a sync function, so use call_soon_threadsafe
            # if needed; for asyncio single-thread scenarios, create_task works
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(queue.put_nowait, msg)
            except Exception:
                pass

        builtins.print = capturing_print
        try:
            result = await coro_fn()
        finally:
            builtins.print = original_print

        return result
