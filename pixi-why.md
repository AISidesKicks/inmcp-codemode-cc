## 📝 INTERNAL MEMO: Transitioning from Conda to Pixi for our Labs 

## 🎯 Objective
To ensure 100% reproducibility of AI and data science experiments in the lab, drastically speed up environment creation, and enable a clean all-in-one project backup strategy via a single local directory.
------------------------------
## 🚀 Why Pixi is Ideal for Our Lab

   1. Guaranteed Scientific Reproducibility (pixi.lock)
   Scientific results must be identical across runs. Conda dynamically resolves dependencies every time you install from an environment.yml file, which can introduce newer sub-versions of packages and silently alter experiment metrics. Pixi generates a strict pixi.lock file. This guarantees that every researcher on the team gets the exact same bit-for-bit environment today, tomorrow, or in three years.
   2. Zero Waiting Time (Rust-Powered Speed)
   Resolving complex AI environments with CUDA and PyTorch takes minutes in Conda and frequently ends in dependency conflicts. Pixi is built in Rust. It runs parallel downloads and solves dependencies in seconds, keeping researchers focused on code, not installation bars.
   3. Cross-Platform Standardization
   A single pixi.toml file natively supports Linux (compute servers), Windows (workstations), and macOS (Apple Silicon laptops), making it seamless to share projects across different hardware in the lab.

------------------------------
## 🔬 Multiple Environments for Free

Pixi lets us define any number of named environments via `[feature.<name>]` tables plus an `[environments]` table — one project, many isolated dep sets, zero runtime overhead. You only pay for the dep set you activate.

Planned split for this lab:

   - `default` — the shared core (python, pip, ruff, litellm, openai, python-dotenv, pydantic).
   - `experiment` — adds smoke-test tooling (pytest, httpx/httpx-sse, asyncio, anyio, diskcache) → `pixi run -e experiment …`.
   - `benchmarks` — adds benchmark/evals tooling (deepeval, promptfoo, ragas, trulens stack, promptrefiner, promptimal, pandas/matplotlib/seaborn, rapidfuzz) → `pixi run -e benchmarks …`.

Bonus: parallel env installs via `pixi install --environment {project:all}`, and the rest of our workflows keep using the lean `default` env.

------------------------------
## 📂 Local Environments & Lab Backup Strategy
Unlike Conda, which buries environments deep within the user's system directory (~/miniconda3/envs/...), Pixi installs the entire environment inside a local .pixi folder directly within your project directory.

📁 my_lab_project/            <-- This main folder is all we need to manage!
├── 📄 pixi.toml              # Project definition and package list
├── 📄 pixi.lock              # Locked exact versions (critical for text/Git backup)
├── 📁 src/                   # Your Python scripts and research code
├── 📁 datasets/              # Experimental data / datasets
└── 📁 .pixi/                 # CONTAINS THE ENTIRE LOCAL PYTHON INTERPRETER & LIBRARIES!

## 💡 Best Practices for Lab Backups:
For daily work and code sharing (e.g., via GitHub / GitLab), the .pixi directory should be ignored because it contains gigabytes of binary files. Only pixi.toml and pixi.lock are tracked. When a colleague clones the repository, they just type pixi install, and Pixi recreates the exact environment locally in seconds.
However, for long-term lab archiving (e.g., after publishing a paper or completing a grant), we often need an absolute "freeze" that works 100% offline without relying on external package servers:

* The Hard-Backup Solution: Simply copy the entire my_lab_project parent folder (including the hidden .pixi folder) directly to your lab NAS, cold storage, or external drive.
* Why this works: Pixi does not use absolute system paths. All binary links within .pixi are relative to the project root. If you load this backup onto a machine with the same OS architecture five years from now, your scripts and tools will run instantly without an internet connection using:

pixi run python src/main.py
