"""Recipe service control plane (FastAPI, runs in k8s).

Scaffold only — the app, DB, storage, upload/worker/review APIs, and Mealie injection
are built in recipe-pipeline #4–#8. Depends on ``recipe_pipeline_shared`` for the Recipe
model; deliberately has NO Apple Vision / pyobjc deps (the server never OCRs).
"""

__version__ = "0.1.0"
