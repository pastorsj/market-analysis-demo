# GPU base for the tools service: CUDA, PyTorch, RAPIDS, XGBoost, and Sentence Transformers.
# Build once with:  docker build -f services/tools/base.Dockerfile -t market-shock/tools-foundation:phase0 .
FROM nvcr.io/nvidia/pytorch:26.06-py3@sha256:43c018d6a12963f1a1bad85ef8574b5c2a978eec2be0ebcacfb87f69e0d210e1

# RAPIDS 26.08 needs nvCOMP 5.3; the base image's unused DALI package pins 5.1.
RUN python -m pip uninstall --yes nvidia-dali-cuda130 \
    && python -m pip install --no-cache-dir --extra-index-url https://pypi.nvidia.com \
      cudf-cu13==26.8.1 cugraph-cu13==26.8.0 cuml-cu13==26.8.0 cuvs-cu13==26.8.1 \
      xgboost==3.4.1 transformers==5.17.0 sentence-transformers==5.7.0 pytest==9.1.1 \
    && python -m pip check
