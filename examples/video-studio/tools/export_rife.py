"""Export the RIFE midpoint model (hzwer/arXiv2020-RIFE, MIT) to ONNX.

This file holds no weights. It is the one place torch is used, and it runs only in its own venv:

    ~/venvs/rife-convert   Python 3.12, torch 2.14.1, onnx 1.23.2, onnxruntime 1.31.1, numpy, pillow

Inputs, all outside the repo:
  SRC      a checkout of hzwer/arXiv2020-RIFE (revision 5d8adbdd40e12c2c8f91930eff838aebe561c086 in MODELS.md)
  WEIGHTS  train_log/flownet.pkl from the 'RIFE model' zip in that repo's README (see MODELS.md)

Usage:
    ~/venvs/rife-convert/bin/python tools/export_rife.py SRC WEIGHTS OUT.onnx

The IFNet forward ignores its timestep argument, so the exported file is the midpoint (t = 0.5) only.
Input 'frames': (1, 6, H, W) float32 in 0..1, the two frames stacked along channels (img0 then img1),
H and W multiples of 32. Output 'merged': (1, 3, H, W) float32 in 0..1.
"""
import sys

import torch
import torch.nn as nn

OPSET = 17


class Midpoint(nn.Module):
    def __init__(self, net):
        super().__init__()
        self.net = net

    def forward(self, x):
        _, _, merged, _, _, _ = self.net(x, scale=[4, 2, 1], timestep=0.5)
        return merged[2]


def main(src, weights, out):
    sys.path.insert(0, src)
    from model.IFNet import IFNet

    net = IFNet()
    sd = torch.load(weights, map_location="cpu")
    net.load_state_dict({k.replace("module.", ""): v for k, v in sd.items()})
    net.eval()
    dummy = torch.rand(1, 6, 288, 512)
    torch.onnx.export(
        Midpoint(net).eval(),
        (dummy,),
        out,
        opset_version=OPSET,
        input_names=["frames"],
        output_names=["merged"],
        dynamic_axes={"frames": {2: "height", 3: "width"}, "merged": {2: "height", 3: "width"}},
        dynamo=False,
    )
    print("exported", out, "opset", OPSET)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
