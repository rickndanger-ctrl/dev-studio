# Models

Models are never stored in the repo. Each one is downloaded once, outside the repo, and pinned by
revision and SHA256.

## Depth Anything V2 Small (ONNX, fp32)

| Field | Value |
|---|---|
| Repo | onnx-community/depth-anything-v2-small |
| File | onnx/model.onnx (fp32) |
| Revision | 4472b7362082ad9968fee890ca0f1e5aca36b93d |
| Download URL | https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/4472b7362082ad9968fee890ca0f1e5aca36b93d/onnx/model.onnx |
| Local path | ~/models/depth-anything-v2-small/model.onnx |
| Size | 99,060,839 bytes |
| SHA256 | afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c |
| License (ONNX repo) | apache-2.0 (card metadata `cardData.license`) |
| License (upstream) | apache-2.0 (depth-anything/Depth-Anything-V2-Small card) |
| Upstream model | Depth Anything V2, Small variant (the `vits` encoder) |
| Downloaded | 2026-10-27 |

Checked: the downloaded file's size and SHA256 match the table above. The download was done once,
with `curl` to the pinned URL.

Not chosen: `onnx/model_fp16.onnx` (49.6 MB), because the owner asked for fp32 for CPU-provider
safety. The Base and Large Depth Anything V2 variants are non-commercial and are not used.
