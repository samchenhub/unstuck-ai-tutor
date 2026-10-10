# Answer-quality eval

Question: does seeing the screen make the tutor's answers better?

## Method

1. Pick 3 videos whose meaning depends on what is on screen (a math lecture with a whiteboard, a coding tutorial, a slide-based talk). Import each through the app and wait until its status is "ready".
2. Write about 20 items in `questions.json` (format: `questions.example.json`). Each item is a timestamp, a question a learner would plausibly ask there, and 2–4 key points a good answer must contain. Write key points from watching the video, not from the model's output. Include questions that need the screen ("what does this term mean?") and ones that don't.
3. Run `cd backend && uv run python ../eval/run_eval.py ../eval/questions.json`.
4. Every item is answered twice with the same prompt pipeline:
   - `transcript_only`: no frame descriptions and no live frame
   - `full`: keyframe descriptions, OCR text and the live frame at the pause point
5. An LLM judge marks which key points each answer covers. Score = covered / total. The report lists the mean per mode, per-question scores and both answers.

## Caveats

- The judge is the same free model as the tutor, so absolute numbers are noisy. Compare the two modes against each other, not against other systems.
- About 20 items is a small sample: treat a gap of a few points as noise and look at the per-question table for clear wins and losses.
- Retrieval ranking in the `transcript_only` arm uses embeddings that include frame text, so visual information can still influence which segments are retrieved (though not the content passed to the model).
- The `full` arm may reuse cached live-frame descriptions (`frame_cache`) produced for earlier questions at the same second, so those descriptions were written for a different question.
