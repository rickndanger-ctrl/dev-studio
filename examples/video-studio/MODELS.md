# Models

Models are never stored in the repo. Each one is downloaded once, outside the repo, and pinned by
revision and SHA256. The code never downloads a model at run time.

| Model | Used by | File | License | Size | Runs on |
|---|---|---|---|---|---|
| Depth Anything V2 Small (ONNX, fp32) | `stillmove render --backend da2-small`, `stillmove mesh --depth da2-small` | `onnx/model.onnx` | Apache-2.0 | 99,060,839 bytes | CPU (onnxruntime) |
| IS-Net general use (ONNX) | `stillmove matte --backend isnet`, `--matte isnet` | `isnet-general-use.onnx` | MIT (rembg packaging), Apache-2.0 (weights) | 178,648,008 bytes | CPU (onnxruntime) |
| RIFE midpoint (ONNX, from hzwer/arXiv2020-RIFE weights) | `stillmove interp --backend rife`, `stillmove render --interp-backend rife` | `rife_ifnet_midpoint.onnx` | MIT (code and weights, per the repo's LICENSE and README) | 40,443,991 bytes | CPU (onnxruntime) |
| Real-ESRGAN general x4 v3 (ONNX, SRVGG compact; for upscaling) | `stillmove` esrgan upscale backend (VS-16, not yet built) | `realesr-general-x4v3.onnx` | BSD-3-Clause via the repo LICENSE; the release has no file-level statement | 4,866,418 bytes (ONNX), 4,885,111 bytes (pth) | CPU (onnxruntime), tiled |

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

## RIFE midpoint (ONNX, from the original RIFE weights)

| Field | Value |
|---|---|
| Code | hzwer/arXiv2020-RIFE (now served as ECCV2022-RIFE), MIT, revision 5d8adbdd40e12c2c8f91930eff838aebe561c086 (main, 2025-09-10) |
| Weights download | Google Drive file 1h42aGYPNJn2q8j_GVkS_yDu__G_UZ2GX, the "RIFE model" link in the repo README (https://drive.google.com/file/d/1h42aGYPNJn2q8j_GVkS_yDu__G_UZ2GX/view). Fetched once with curl to drive.usercontent.google.com with confirm=t. |
| Local zip | ~/models/rife/src/rife_model.bin, 39,799,730 bytes, SHA256 620aad3e86f51bfb04b81d71a1246a08932366f8b03a1df254824b33cf3fbfba |
| Weights inside the zip | train_log/flownet.pkl, 42,901,317 bytes, SHA256 2b48d6f7c7c09c109ad50372e9d9dc722a75859dc20e8832a62826ecede35ad3 |
| Key check | flownet.pkl matches model/IFNet.py of the code revision above exactly: 190 keys, none missing or extra, no shape mismatch |
| ONNX export | ~/models/rife/rife_ifnet_midpoint.onnx, 40,443,991 bytes, SHA256 a02e9572a6bc641100145f9a3883085acfeed204a40a69cb02e25a2d8d7621c4, opset 17, dynamic height and width (tools/export_rife.py) |
| Local path used by the code | ~/models/rife/rife_ifnet_midpoint.onnx |
| Downloaded and exported | 2026-10-28 |
| Export environment | ~/venvs/rife-convert, Python 3.12, torch 2.14.1, onnx 1.23.2, onnxruntime 1.31.1 (torch is not installed in the stillmove venv) |
| Verification | ONNX against torch, max difference on the 0-1 scale: 1.0e-4 (0.026 of 255) on two frames from the owner's logo reveal (1080x1080, padded to 1088), 2.0e-5 (0.005 of 255) on a generated 320x192 pair |

**Limit: midpoint only.** The IFNet forward ignores its timestep argument, so the file makes only the
frame halfway between two frames. The backend gives 2x (t = 0.5) directly, and 4x from the same
midpoint: t = 0.25 is mid(a, mid(a, b)) and t = 0.75 is mid(mid(a, b), b). Any other fraction is refused.
A 4x interpolation of one pair makes 3 model calls, because the midpoint is cached. Arbitrary timesteps
would need the IFNet_m model or another file; neither is installed.

The inputs must be multiples of 32: the backend pads by edge replication and crops the output back.

Upstream LICENSE (hzwer/arXiv2020-RIFE, revision above), copied as it was downloaded:

```
MIT License

Copyright  (c)  Megvii  Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The weights came from the "RIFE model" link in the arXiv2020-RIFE README, not from a model hub. The
Practical-RIFE README says its trained-model links are under MIT too; those weights are not used here.

## Real-ESRGAN general x4 v3 (ONNX, SRVGG compact)

| Field | Value |
|---|---|
| Code | xinntao/Real-ESRGAN, tag v0.2.5.0, commit 685d429c81888252bdb10f56c7754baededc3823, `realesrgan/archs/srvgg_arch.py` (SRVGGNetCompact) |
| Weights URL | https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth (release v0.2.5.0, asset realesr-general-x4v3.pth, GitHub size 4,885,111 bytes) |
| License | **BSD-3-Clause via the repo LICENSE; the release has no file-level statement.** The release notes and README say nothing about a separate license for this file. |
| Local pth | ~/models/realesrgan/src/realesr-general-x4v3.pth, 4,885,111 bytes, SHA256 8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292 (downloaded 2026-10-28, once) |
| ONNX export | ~/models/realesrgan/realesr-general-x4v3.onnx, 4,866,418 bytes, SHA256 a93b2f013c19bc043e975915ffa4d8dc9ed8270ec9fbc87dcec21bce42db4d61, opset 17, dynamic height and width. Input `image` (1, 3, H, W) float32 0..1 RGB; output `upscaled` (1, 3, 4H, 4W) float32. |
| Architecture | 32 body convolutions (num_feat 64, PReLU, upscale 4), 1,213,296 parameters. The checkpoint loads strictly only with 32; the repo's inference script at v0.2.5.0 builds 16 and would not load this file. |
| Verification (ONNX vs torch, 0-1 scale) | owner logo, 512x512 crop: max 5.8e-6 (0.0015 of 255); generated 320x200 gradient with disc: max 6.5e-5 (0.017 of 255) |

Only the x4 general model is installed. The x4plus model is not downloaded (VS-16 skips quality mode).

The LICENSE text of the repo at v0.2.5.0, copied as downloaded:

```
BSD 3-Clause License

Copyright (c) 2021, Xintao Wang
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
