"""Export Real-ESRGAN realesr-general-x4v3 (SRVGG compact, xinntao/Real-ESRGAN, BSD-3) to ONNX.

This file holds no weights. torch is used only here, in the venv that also runs the RIFE export:

    ~/venvs/rife-convert   Python 3.12, torch 2.14.1, onnx 1.23.2, onnxruntime 1.31.1, numpy, pillow

Input, outside the repo: the weights realesr-general-x4v3.pth from release v0.2.5.0 (MODELS.md).

Usage:
    ~/venvs/rife-convert/bin/python tools/export_esrgan.py WEIGHTS.pth OUT.onnx

The network is SRVGGNetCompact from realesrgan/archs/srvgg_arch.py at v0.2.5.0, copied here without the
basicsr registry. The checkpoint loads strictly only with 32 body convolutions, so num_conv is 32.
Input 'image': (1, 3, H, W) float32 RGB in 0..1. Output 'upscaled': (1, 3, 4H, 4W) float32.
"""
import sys

import torch
import torch.nn.functional as F
from torch import nn

OPSET = 17
NUM_CONV = 32  # the checkpoint's body length; the repo's own inference script builds 16 and does not load it


class SRVGGNetCompact(nn.Module):
    def __init__(self, num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=NUM_CONV, upscale=4):
        super().__init__()
        self.upscale = upscale
        self.body = nn.ModuleList()
        self.body.append(nn.Conv2d(num_in_ch, num_feat, 3, 1, 1))
        self.body.append(nn.PReLU(num_parameters=num_feat))
        for _ in range(num_conv):
            self.body.append(nn.Conv2d(num_feat, num_feat, 3, 1, 1))
            self.body.append(nn.PReLU(num_parameters=num_feat))
        self.body.append(nn.Conv2d(num_feat, num_out_ch * upscale * upscale, 3, 1, 1))
        self.upsampler = nn.PixelShuffle(upscale)

    def forward(self, x):
        out = x
        for layer in self.body:
            out = layer(out)
        out = self.upsampler(out)
        base = F.interpolate(x, scale_factor=self.upscale, mode="nearest")
        return out + base


def main(weights, out):
    net = SRVGGNetCompact()
    doc = torch.load(weights, map_location="cpu")
    net.load_state_dict(doc.get("params_ema", doc.get("params", doc)))  # strict: a wrong layout fails here
    net.eval()
    torch.onnx.export(
        net,
        (torch.rand(1, 3, 128, 192),),
        out,
        opset_version=OPSET,
        input_names=["image"],
        output_names=["upscaled"],
        dynamic_axes={"image": {2: "height", 3: "width"}, "upscaled": {2: "height4", 3: "width4"}},
        dynamo=False,
    )
    print("exported", out, "opset", OPSET)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
