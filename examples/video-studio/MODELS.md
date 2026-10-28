# Models

Models are never stored in the repo. Each one is downloaded once, outside the repo, and pinned by
revision and SHA256. The code never downloads a model at run time.

| Model | Used by | File | License | Size | Runs on |
|---|---|---|---|---|---|
| Depth Anything V2 Small (ONNX, fp32) | `stillmove render --backend da2-small`, `stillmove mesh --depth da2-small` | `onnx/model.onnx` | Apache-2.0 | 99,060,839 bytes | CPU (onnxruntime) |
| IS-Net general use (ONNX) | `stillmove matte --backend isnet`, `--matte isnet` | `isnet-general-use.onnx` | MIT (rembg packaging), Apache-2.0 (weights) | 178,648,008 bytes | CPU (onnxruntime) |

The `fake` depth and `fake` matte need neither model, so the tests run without them. The doctor
reports each model as WARN when it is absent and FAIL when its SHA256 does not match.

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

## IS-Net general use (ONNX, for matting)

| Field | Value |
|---|---|
| Model | isnet-general-use (IS-Net, general-use weights) |
| File | isnet-general-use.onnx |
| Download URL | https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx |
| Local path | ~/models/isnet-general-use/isnet-general-use.onnx |
| Size | 178,648,008 bytes |
| SHA256 | 60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a |
| MD5 | fc16ebd8b0c10d971d3513d564d01e29 |
| License (packaging) | MIT (rembg) |
| License (weights) | Apache-2.0 (weights from xuebinqin/DIS) |
| Runtime | onnxruntime 1.31.1, in ~/venvs/video-studio (Python 3.14) |
| Input | RGB resized to 1024x1024, x/255 - 0.5 (std 1.0), NCHW float32 |

Used by `stillmove matte --backend isnet` for the mask. The file is never stored in the repo. The
doctor checks its presence and SHA256.

Second machine: the same file was downloaded once on 2026-10-27 from the URL above into
`~/models/isnet-general-use/isnet-general-use.onnx`. Its size (178,648,008 bytes) and SHA256 match the
table. It runs there with onnxruntime 1.19.2 on Python 3.9.6, about 2.5 s per 1254x1254 matte with a
peak of about 1 GB.

## Per-machine notes

- **Mac mini (Python 3.14):** onnxruntime 1.31.1 in the video-studio venv. IS-Net is downloaded here
  (the file and SHA256 above). Both models are used from the same `~/models/` folder.
- **Second machine (Python 3.9.6):** onnxruntime 1.19.2 in the stillmove venv. Depth Anything V2 Small
  is downloaded here (fp32, 99,060,839 bytes). IS-Net was also downloaded here: about 2.5 s per
  1254x1254 matte with a peak of about 1 GB.

Nothing else is needed: onnxruntime is the only runtime, and the models run on the CPU provider.
