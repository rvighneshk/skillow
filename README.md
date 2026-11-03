# Skillow
### From watching to doing.

**Skillow is a multimodal AI learning assistant that turns practical video tutorials into structured lessons, compares a learner's real-world progress against the complete procedure, and explains difficult concepts with contextual answers and illustrative visuals.**

It is designed to help learners move beyond passively watching tutorials: understand the process, apply the knowledge, and overcome practical challenges.

---

## Table of contents

- [The problem](#the-problem)
- [What Skillow does](#what-skillow-does)
- [Core features](#core-features)
  - [Understand — complete tutorial lessons](#1-understand--complete-tutorial-lessons)
  - [Compare — personalised progress review](#2-compare--personalised-progress-review)
  - [Build My Guide — actionable next steps](#3-build-my-guide--actionable-next-steps)
  - [Ask & Visualise — contextual explanations](#4-ask--visualise--contextual-explanations)
- [AI architecture](#ai-architecture)
- [Lesson-generation pipeline](#lesson-generation-pipeline)
- [Evidence, accuracy and safety](#evidence-accuracy-and-safety)
- [Demo tutorials](#demo-tutorials)
- [Technology stack](#technology-stack)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Run Skillow locally](#run-skillow-locally)
- [Testing](#testing)
- [Deployment](#deployment)
- [Limitations](#limitations)
- [Acknowledgements](#acknowledgements)

---

## The problem

Video tutorials are excellent demonstrations, but learning from them can be difficult:

- Important actions are spread across a long video.
- Learners must repeatedly pause, rewind, and search for the relevant moment.
- A tutorial may show a procedure without explaining every intermediate action.
- Generic AI answers may not match the exact tutorial or the learner's current setup.
- Learners often need to know what they have already completed, what is missing, and what to do next.
- Visual explanations can help, but an inaccurate technical diagram can be misleading.

Skillow brings tutorial understanding, progress review, and contextual explanations into one learning workspace.

## What Skillow does

Skillow supports a workflow from tutorial to practical application:

1. **Understand:** Upload a tutorial or provide a supported video URL. Skillow extracts a structured lesson from the video and its spoken content.
2. **Review the complete procedure:** Explore objectives, prerequisites, tools, materials, safety information, and chronological stages.
3. **Compare:** Upload a photo of your current work and optionally describe it. Skillow reviews your situation against the full lesson.
4. **Build My Guide:** Receive an actionable, personalised written guide based on the tutorial and the learner's current progress.
5. **Ask & Visualise:** Ask questions about the tutorial and receive a contextual written explanation with an optional illustrative image.

---

## Core features

### 1. Understand — complete tutorial lessons

Skillow transforms a tutorial into a structured learning reference rather than a short summary.

A generated lesson can include:

- Learning objectives
- Prerequisites
- Required tools and materials
- Relevant safety warnings and precautions
- Chronologically ordered stages
- Detailed stage instructions and smaller actions/substeps
- Spoken explanations associated with the procedure
- Expected outcomes and observations
- Verification checks
- Relevant visual observations and visible items
- Timestamps and confidence/verification information when available

Lesson depth follows the content of the tutorial. A short video may reasonably produce a few stages; a longer tutorial can contain many more. The pipeline does not intentionally force every video into the same number of steps. The current structured output accepts up to 30 stages, while the model is instructed to preserve meaningful actions and substeps instead of targeting a fixed count.

The lesson is intended to preserve the tutorial's important instructional context and help learners follow the procedure without constantly searching the original video.

### 2. Compare — personalised progress review

Compare evaluates a learner's current setup against the **complete extracted tutorial procedure**, not just one matched frame.

**Input**
- A learner-uploaded photo
- An optional description of the current situation
- The generated lesson and available transcript/video context

The comparison is designed to distinguish:

- Work that appears completed
- Work that appears correct based on available evidence
- Differences from the tutorial
- Missing or potentially incorrect actions/components
- Details that cannot be verified from the supplied photo
- Issues requiring attention
- Recommended next actions
- Reasons behind recommendations
- Safety considerations

The comparison is evidence-aware: it should not claim that a component is correctly wired, assembled, or functioning merely because an object is visible. When the photo does not establish something, Skillow should identify it as uncertain rather than inventing certainty.

### 3. Build My Guide — actionable next steps

Build My Guide converts the comparison into a practical written plan adapted to the learner's current progress.

The guide may include:

- An overview of the recommended approach
- Prerequisites or preparation
- Ordered action steps
- Why a step matters
- Expected outcomes or checks
- Safety notes
- A distinction between tutorial-derived instructions, adapted instructions, and general knowledge
- Browser-based text-to-speech for listening to guide steps

The guide is grounded in the tutorial and the comparison findings. It should avoid repeating completed work unnecessarily and focus on what the learner needs to do next.

**Build My Guide is text-first.** It does not generate a technical image as a substitute for precise written instructions.

### 4. Ask & Visualise — contextual explanations

Ask & Visualise lets learners ask questions about the tutorial, a component, a technique, or a concept.

The response can use:

- The generated lesson
- Relevant transcript context
- Available tutorial information
- General knowledge, clearly distinguished from what the tutorial demonstrates

Answers are presented as structured, readable Markdown: headings, paragraphs, lists, emphasis, and code where relevant.

An optional generated illustration accompanies the explanation when useful. **Generated visuals are illustrative aids, not verified technical diagrams, measurements, wiring instructions, or proof of correctness.** The written answer remains available if image generation fails.

---

## AI architecture

Skillow coordinates specialised Meta AI capabilities and local processing.

| Component | Responsibility |
|---|---|
| **Muse Spark** | Understand tutorial video and sampled frames; structure lessons; verify selected stages; reason about learner photos and progress; answer tutorial-specific questions; generate comparison findings and personalised guidance. |
| **Muse Voice Transcribe** | Transcribe tutorial speech into text for lesson extraction and contextual reasoning. |
| **SAM 3.1** | Detect and segment prompted objects in learner images, returning object/mask/region evidence that can support visual analysis. |
| **Muse Image** | Generate conceptual or instructional illustrations for Ask & Visualise. |
| **FFmpeg / FFprobe** | Inspect video duration and extract audio or frames for analysis. |
| **Pillow / image processing** | Handle image and segmentation-mask processing and visual overlays where implemented. |
| **SQLite** | Persist tutorial records, lesson data, transcripts, stages, and application state. |

SAM is a supplementary visual-analysis component. Segmentation can indicate where an object appears in an image; it cannot establish electrical continuity, correct wiring, mechanical integrity, or whether something works.

---

## Lesson-generation pipeline

The lesson pipeline combines video understanding, speech transcription, structured extraction, and selected stage checks.

1. **Receive the tutorial**  
   Skillow records the tutorial and starts background processing.

2. **Inspect the video**  
   FFprobe obtains video duration and checks that the media can be processed.

3. **Transcribe the audio**  
   FFmpeg extracts audio for Muse Voice Transcribe. Transcription is bounded by processing limits; failures can be handled without automatically discarding the video-analysis path.

4. **Understand the video**  
   When native video input is available and configured, Muse Spark receives the video URL for whole-video analysis. If that path is unavailable or fails, Skillow extracts and samples frames, then analyzes those frames with the transcript.

5. **Extract the lesson**  
   Muse Spark produces structured lesson information, including objectives, prerequisites, tools, materials, safety, stages, timestamps, observations, expected outcomes, and checks. Instructions emphasize chronological order, meaningful actions, detailed explanations, and avoiding unsupported invention.

6. **Verify selected stages**  
   Skillow extracts frames around selected stage timestamps and asks Muse Spark to check whether stage claims are supported by the footage. This is a targeted check, not exhaustive frame-by-frame verification of every action.

7. **Normalize and persist**  
   The application normalizes stage ordering and fields, retains uncertainty where appropriate, and saves the lesson, transcript, and stage records in SQLite.

8. **Use the lesson downstream**  
   Compare, Build My Guide, and Ask & Visualise use the generated lesson and relevant context rather than treating the original video as an unstructured prompt every time.

---

## Evidence, accuracy and safety

Skillow is designed around evidence-aware learning assistance.

- **Tutorial evidence:** Information directly shown or spoken in the source tutorial.
- **Learner evidence:** What can be observed in the learner's uploaded photo or description.
- **General knowledge:** Explanations or precautions not explicitly demonstrated by the tutorial; these should be distinguished from tutorial evidence.
- **Inference:** A possible interpretation that should not be presented as confirmed fact.
- **Unverifiable information:** Details that cannot be established from the available video, image, or description.

Important safeguards:

- A visual match is not proof of functional correctness.
- SAM masks are object-region evidence, not proof that a step was performed correctly.
- Generated illustrations are conceptual and may not reproduce exact real-world geometry or component specifications.
- Technical measurements, pin assignments, wiring relationships, or safety-critical details should not be invented.
- Uncertainty should be stated explicitly.
- Safety-critical work may require qualified supervision or professional assistance.

Skillow is a learning aid, not a replacement for professional judgement, manufacturer documentation, or appropriate safety procedures.

---

## Demo tutorials

The application includes demonstration workflows to make its features easy to explore.

| Demo | Tutorial scenario | Example question |
|---|---|---|
| **Plumbing** | Cleaning a sink P-trap | Ask & Visualise: “How does the debris clog in P-trap?” Compare / Guide: “How to open my P-trap?” |
| **Cooking** | Tamil cooking tutorial | Ask & Visualise: “Why do we need to add ghee?” Compare / Guide: “Why is my rice still soggy even after 10 minutes of cooking at low heat?” |
| **Arduino** | Ultrasonic distance sensor / LED demonstration | Ask & Visualise: “Why do we need to use resistors?” Compare / Guide: “Why does my LED not lighting up?” |

These are demo-specific example prompts. Custom uploads use generic question placeholders so learners can ask about their own tutorials.

---

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI |
| Frontend | Jinja templates, HTML, CSS, JavaScript |
| UI typography | Poppins |
| Database | SQLite |
| Video processing | FFmpeg, FFprobe |
| AI | Meta Muse Spark, Muse Voice Transcribe, SAM 3.1, Muse Image |
| Image processing | Pillow and mask/overlay utilities |
| Background processing | Application-managed tutorial jobs and status polling |
| Deployment | ASGI server; Render-compatible configuration |
| Testing | Python unittest-based service tests with Meta API test doubles |

---

## Project structure

```text
skillow/
├── app/
│   ├── main.py                 # FastAPI application entry point
│   ├── routes.py               # Web routes and feature workflows
│   ├── config.py               # Environment-based settings
│   ├── database.py              # SQLite setup/session handling
│   ├── models.py                # Database models
│   ├── schemas.py                # Data schemas
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── lesson.html
│   │   ├── comparison.html
│   │   └── explainer.html
│   ├── static/
│   │   ├── css/app.css
│   │   └── js/app.js
│   └── services/
│       ├── meta_client.py        # Meta API client
│       ├── video_processor.py    # FFmpeg media processing
│       ├── lesson_analysis.py    # Analysis orchestration
│       ├── tutorial_analyzer.py  # Lesson prompts, schema and normalization
│       ├── lesson_pipeline.py    # Tutorial processing pipeline
│       ├── jobs.py               # Background job handling
│       ├── comparison.py          # Comparison and guide generation
│       ├── segmentation.py       # SAM segmentation and overlays
│       ├── qa.py                  # Ask & Visualise answers
│       ├── diagrams.py            # Muse Image generation
│       └── domains.py              # Domain profiles and safety context
├── config/
│   ├── demo_cases.json
│   └── domain_profiles.json
├── tests/
│   ├── fake_meta.py
│   ├── stubs.py
│   └── test_services.py
├── assets/
├── storage/                        # Runtime media and generated files
├── skillswap.db                    # Local SQLite database (runtime)
├── requirements.txt
├── .env.example
└── README.md
```

Runtime files such as `.env`, the local database, uploaded videos, and generated images are not intended to be committed as part of the source release.

---

## Getting started

### Prerequisites

- Python 3.11 or newer
- FFmpeg and FFprobe installed and available on `PATH`
- A Meta API key with access to the configured models
- Git (optional, for cloning)
- A public HTTPS URL or configured public base URL for native video processing of locally uploaded videos

### 1. Clone or extract the project

```bash
git clone <repository-url>
cd skillow
```

Or extract the project ZIP and open the folder in PyCharm.

### 2. Create a virtual environment

**Windows PowerShell**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Windows Command Prompt**

```bat
python -m venv .venv
.venv\Scripts\activate
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` and set your credentials and secret key. Never commit `.env`.

### 5. Install FFmpeg

Verify that both commands work:

```bash
ffmpeg -version
ffprobe -version
```

If either command is unavailable, install FFmpeg and add its `bin` directory to your system `PATH`.

---

## Configuration

The application reads configuration from environment variables. See **`.env.example`** for the exact supported names and defaults.

Typical required configuration:

```dotenv
MODEL_API_KEY=your_meta_api_key
SECRET_KEY=replace_with_a_long_random_secret
```

Depending on the deployment and enabled processing paths, configure:

- Meta model identifiers
- `PUBLIC_BASE_URL` for public access to uploaded media
- Native video-analysis settings
- Upload and processing limits
- Storage paths and application options

Do not put real credentials in source code, README examples, screenshots, or public repositories.

---

## Run Skillow locally

Start the FastAPI application from the project root:

```bash
uvicorn app.main:app --reload
```

Open:

**http://127.0.0.1:8000**

For a non-development run:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Keep the terminal running while using the application. Background jobs, media processing, and API calls are handled by the application.

---

## Testing

Run the service test suite:

```bash
python -m tests.test_services
```

Compile Python modules:

```bash
python -m compileall -q app tests
```

Check frontend JavaScript syntax (Node.js required):

```bash
node --check app/static/js/app.js
```

The automated suite uses test doubles/stubs for external Meta services. **Passing local tests does not mean live Muse API calls, model quality, or every deployed upload path has been tested.** For a release smoke test, verify:

1. Homepage and demo tutorial pages load.
2. A short video completes lesson generation.
3. A longer tutorial retains detailed actions and substeps.
4. The lesson displays objectives, tools/materials, safety, and ordered procedure.
5. Compare accepts a learner photo and description and returns evidence-aware findings.
6. SAM output is displayed as supplementary segmentation evidence.
7. Build My Guide returns practical written steps.
8. Ask & Visualise returns a readable answer and, when available, an illustrative image with its disclaimer.
9. Upload, loading, failure, and retry states behave as expected.
10. The app works at desktop and mobile viewport sizes.

---

## Deployment

Skillow is a FastAPI application and can be deployed to an ASGI-compatible host such as Render.

Deployment considerations:

- Set the start command to an ASGI server, for example:

  ```bash
  uvicorn app.main:app --host 0.0.0.0 --port $PORT
  ```

- Set `MODEL_API_KEY` and a strong `SECRET_KEY` in the hosting provider's environment configuration.
- Set `PUBLIC_BASE_URL` to the deployed HTTPS origin when Meta needs to fetch uploaded video files.
- Ensure FFmpeg/FFprobe are installed in the runtime environment.
- Provide writable storage for uploaded videos and generated images.
- Use persistent storage for SQLite and media if the hosting environment has ephemeral filesystems.
- Do not expose API credentials in frontend JavaScript.
- Check model access, request limits, video duration/size limits, and hosting timeouts.
- Review logs for failed transcription, video analysis, segmentation, and image generation.

---

## Limitations

- Video understanding depends on model capability, source quality, sampling, and processing limits.
- When native full-video analysis is unavailable, sampled-frame analysis may miss brief actions or details between frames.
- Transcription can fail or omit speech, especially with noise, accents, or unsupported audio.
- Stage verification checks selected timestamps; it is not exhaustive frame-by-frame validation.
- A generated lesson can still omit or combine actions. Learners should cross-check important details against the original video.
- A single learner photo may not reveal hidden connections, measurements, internal components, or whether a system functions.
- SAM detects/segments visual regions but does not validate correctness or operation.
- Generated images are illustrative and may contain visual inaccuracies; do not use them as exact engineering drawings.
- AI API availability, latency, quotas, and costs depend on the configured Meta services and account.
- Demo tutorials and prompts are examples, not universal instructions.

---

## Acknowledgements

Skillow was developed for the **Meta AI Developer Hackathon 2026** using Meta's AI capabilities.

Built with Python, FastAPI, SQLite, FFmpeg, and the Meta Muse/SAM model ecosystem.

---

## Project tagline

> **From watching to doing.**  
> Understand the process. Apply the knowledge. Overcome the challenges.
