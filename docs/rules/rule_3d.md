<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 3d. A symptom on one screen is not a fault in that screen's code

"The uploads stopped" is a statement about a PIPELINE, and a pipeline runs
across machines. Before reading the upload code, establish where the work
physically stops:

1. **Which machine performs this step?** Processing is per project — MUSIK
   generates on the Linux server, movies run Photoshop on the Windows node.
   **Uploading is ALWAYS the Windows node, for every project.**
2. **Is that machine alive?** A project whose processing happens on the server
   will sail through processing and stop dead at upload the moment the node is
   down, which reads as "the upload feature is broken" and is not.
3. **Only then, the code.**

Ask "what does this design need next, and who provides it" before you ask
"what is wrong with the uploader". Say which of the three you checked.

