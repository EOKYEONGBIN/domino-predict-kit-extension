#!/bin/bash
# =============================================================================
# DoMINO Prediction "Local" 모드용 WSL2 환경 설치
# =============================================================================
# Windows + NVIDIA GPU 노트북/PC의 WSL2(Ubuntu) 안에서 실행한다:
#   bash setup_local_inference.sh
#
# 하는 일:
#   1. 추론 전용 가상환경 ~/venvs/domino_infer 생성
#      (A6000 서버와 같은 버전: torch 2.14.0+cu130, physicsnemo 2.2.2, cuML 26.8)
#   2. physicsnemo 2.2.2 버그 패치 (VTKFileReader에 read_file_attributes 없음)
#   3. ~/domino-ahmedml 에 추론 코드, 설정, 학습된 모델, 실행 스크립트를 GitHub에서 받아 배치
#   4. 설치 확인
#
# 여러 번 실행해도 안전하다 (이미 있는 것은 건너뛰거나 같은 내용으로 덮어씀).
# =============================================================================
set -euo pipefail

VENV=~/venvs/domino_infer
D=~/domino-ahmedml
CODE_RAW=https://raw.githubusercontent.com/EOKYEONGBIN/domino-cfd-pipeline-guide/master
PIPE_RAW=https://raw.githubusercontent.com/EOKYEONGBIN/domino-ahmedml-pipeline/master

echo "=== 0. GPU 확인 ==="
nvidia-smi -L || { echo "WSL 안에서 NVIDIA GPU가 보이지 않습니다. Windows용 NVIDIA 드라이버를 먼저 설치하세요."; exit 1; }

echo "=== 1. 가상환경 ($VENV) ==="
if [ ! -x "$VENV/bin/python" ]; then
    mkdir -p "$(dirname "$VENV")"
    # python3-venv 패키지(sudo 필요)가 없어도 되도록 pip을 직접 설치한다.
    python3 -m venv --without-pip "$VENV"
    wget -q -O /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py
    "$VENV/bin/python" /tmp/get-pip.py -q
fi
source "$VENV/bin/activate"
pip install -q torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cu130
pip install -q nvidia-physicsnemo==2.2.2 pyvista==0.49.0 vtk==9.7.0 torchinfo==1.8.0 \
    nvidia-ml-py scipy==1.18.1 cupy-cuda13x==14.2.0 warp-lang==1.17.0
# cuML이 없으면 physicsnemo의 kNN이 전체 거리 행렬을 만드는 PyTorch 구현으로 대체돼서
# 12GB급 GPU에서 메모리 부족(CUDA out of memory)이 난다.
pip install -q cuml-cu13==26.8.0 --extra-index-url=https://pypi.nvidia.com

echo "=== 2. physicsnemo 2.2.2 패치 ==="
python - <<'PY'
import pathlib, physicsnemo.datapipes.cae.cae_dataset as m
p = pathlib.Path(m.__file__)
src = p.read_text()
anchor = "            return dir_name / fname\n\n        def read_file(self, filename: pathlib.Path)"
method = (
    "            return dir_name / fname\n\n"
    "        def read_file_attributes(self, filename: pathlib.Path) -> dict[str, torch.Tensor]:\n"
    "            # Added by setup_local_inference.sh: VTKFileReader is missing this\n"
    "            # abstract method in physicsnemo 2.2.2, so it can't be instantiated.\n"
    "            # Bare-STL inference has no file-level attributes to read.\n"
    "            return {}\n\n"
    "        def read_file(self, filename: pathlib.Path)"
)
if "Added by setup_local_inference.sh" in src or "VTKFileReader never actually defined" in src:
    print("이미 패치됨")
elif anchor in src:
    p.with_suffix(".py.orig").write_text(src)
    p.write_text(src.replace(anchor, method, 1))
    print("패치 적용:", p)
else:
    raise SystemExit("패치 위치를 찾지 못했습니다 (physicsnemo 버전이 2.2.2가 맞는지 확인하세요).")
PY

echo "=== 3. 추론 코드 / 설정 / 모델 ==="
mkdir -p $D/inference_src $D/configs $D/model $D/scripts $D/requests
for f in predict_on_stl.py utils.py loss.py; do
    wget -q -O $D/inference_src/$f $CODE_RAW/$f
done
wget -q -O $D/configs/real_train_500.yaml $PIPE_RAW/configs/real_train_500.yaml
sed -i "s|/home/YOUR_USER/|$HOME/|g" $D/configs/real_train_500.yaml
wget -q -O $D/model/DoMINO.0.220.mdlus $PIPE_RAW/model/DoMINO.0.220.mdlus
wget -q -O $D/model/scaling_factors.pkl $PIPE_RAW/model/scaling_factors.pkl
# 서버용 실행 스크립트를 받아 이 환경에 맞게 두 줄만 바꾼다:
# 가상환경 → domino_infer, 추론 코드 위치 → ~/domino-ahmedml/inference_src
wget -q -O $D/scripts/run_prediction.sh $CODE_RAW/scripts/run_prediction.sh
sed -i -e 's/\r$//' \
       -e 's|^source ~/venvs/domino/bin/activate$|source ~/venvs/domino_infer/bin/activate|' \
       -e 's|^cd ~/physicsnemo/examples/cfd/external_aerodynamics/domino/src$|cd ~/domino-ahmedml/inference_src|' \
       $D/scripts/run_prediction.sh
chmod +x $D/scripts/run_prediction.sh

echo "=== 4. 확인 ==="
python - <<'PY'
import torch, physicsnemo
from physicsnemo.nn.functional.neighbors.knn.knn import KNN
impls = KNN._get_impls()
print("torch", torch.__version__, "| CUDA", torch.cuda.is_available(), "| physicsnemo", physicsnemo.__version__,
      "| kNN cuML", impls["cuml"].available)
assert torch.cuda.is_available() and impls["cuml"].available
PY
grep -q 'domino_infer' $D/scripts/run_prediction.sh && grep -q 'inference_src' $D/scripts/run_prediction.sh
ls -la $D/model
echo "설치 완료. Kit-CAE의 DoMINO Prediction에서 Server Settings > Local (WSL2) > Connect 를 누르세요."
