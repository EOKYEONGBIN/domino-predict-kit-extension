#!/bin/bash
# =============================================================================
# DoMINO Prediction "Local" 모드용 WSL2 환경 설치
# =============================================================================
# 보통은 익스텐션의 Server Settings > Local (WSL2) > [Install Local Environment]
# 버튼이 이 스크립트를 WSL 안에서 실행한다. 직접 실행하려면 WSL2(Ubuntu) 터미널에서:
#   bash setup_local_inference.sh
#
# 전제: WSL2 + Ubuntu 설치 완료 (관리자 PowerShell: wsl --install -d Ubuntu-24.04 → 재부팅
# → Ubuntu 한 번 실행해 사용자 계정 생성), Windows용 NVIDIA 드라이버 설치. sudo는 필요 없다.
#
# 하는 일:
#   1. 추론 전용 가상환경 ~/venvs/domino_infer 생성
#      (A6000 서버와 같은 버전: torch 2.14.0+cu130, physicsnemo 2.2.2, cuML 26.8)
#   2. physicsnemo 2.2.2 버그 패치 (VTKFileReader에 read_file_attributes 없음)
#   3. ~/domino-ahmedml 에 추론 코드, 설정, 실행 스크립트를 GitHub에서 받아 배치하고,
#      학습된 모델은 이 익스텐션의 model/ 폴더에서 복사
#   4. 설치 확인
#
# 여러 번 실행해도 안전하다 (이미 있는 것은 건너뛰거나 같은 내용으로 덮어씀).
# =============================================================================
set -euo pipefail

# 학습된 모델은 익스텐션 저장소에 함께 들어 있다 (domino_predict/model/)
MODEL_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../model" && pwd)"
VENV=~/venvs/domino_infer
D=~/domino-ahmedml
CODE_RAW=https://raw.githubusercontent.com/EOKYEONGBIN/domino-cfd-pipeline-guide/master
PIPE_RAW=https://raw.githubusercontent.com/EOKYEONGBIN/domino-ahmedml-pipeline/master

# 진행 메시지(=== ...)는 Kit-CAE 익스텐션 상태 줄에 그대로 표시되므로 영어로 쓴다
# (Kit UI 폰트가 한글을 표시하지 못할 수 있음).
echo "=== [1/7] Checking GPU in WSL ==="
nvidia-smi -L || { echo "ERROR: NVIDIA GPU is not visible inside WSL. Install/update the Windows NVIDIA driver first."; exit 1; }

echo "=== [2/7] Creating Python environment ($VENV) ==="
if [ ! -x "$VENV/bin/python" ]; then
    mkdir -p "$(dirname "$VENV")"
    # python3-venv 패키지(sudo 필요)가 없어도 되도록 pip을 직접 설치한다.
    python3 -m venv --without-pip "$VENV"
    wget -q -O /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py
    "$VENV/bin/python" /tmp/get-pip.py -q
fi
source "$VENV/bin/activate"

echo "=== [3/7] Installing PyTorch (about 3GB, several minutes) ==="
pip install -q torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cu130

echo "=== [4/7] Installing PhysicsNeMo and dependencies ==="
pip install -q nvidia-physicsnemo==2.2.2 pyvista==0.49.0 vtk==9.7.0 torchinfo==1.8.0 \
    nvidia-ml-py scipy==1.18.1 cupy-cuda13x==14.2.0 warp-lang==1.17.0

echo "=== [5/7] Installing cuML (about 2GB, several minutes) ==="
# cuML이 없으면 physicsnemo의 kNN이 전체 거리 행렬을 만드는 PyTorch 구현으로 대체돼서
# 12GB급 GPU에서 메모리 부족(CUDA out of memory)이 난다.
pip install -q cuml-cu13==26.8.0 --extra-index-url=https://pypi.nvidia.com

echo "=== [6/7] Patching PhysicsNeMo 2.2.2 and copying model ==="
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
markers = ("Added by setup_local_inference.sh", "Added by patch_physicsnemo.py", "VTKFileReader never actually defined")
if any(m in src for m in markers):
    print("Already patched:", p)
elif anchor in src:
    p.with_suffix(".py.orig").write_text(src)
    p.write_text(src.replace(anchor, method, 1))
    print("Patched:", p)
else:
    raise SystemExit("ERROR: patch anchor not found -- is physicsnemo 2.2.2 installed?")
PY

mkdir -p $D/inference_src $D/configs $D/model $D/scripts $D/requests
for f in predict_on_stl.py utils.py loss.py; do
    wget -q -O $D/inference_src/$f $CODE_RAW/$f
done
wget -q -O $D/configs/real_train_500.yaml $PIPE_RAW/configs/real_train_500.yaml
sed -i "s|/home/YOUR_USER/|$HOME/|g" $D/configs/real_train_500.yaml
cp "$MODEL_SRC/DoMINO.0.220.mdlus" "$MODEL_SRC/scaling_factors.pkl" $D/model/
# 서버용 실행 스크립트를 받아 이 환경에 맞게 두 줄만 바꾼다:
# 가상환경 → domino_infer, 추론 코드 위치 → ~/domino-ahmedml/inference_src
wget -q -O $D/scripts/run_prediction.sh $CODE_RAW/scripts/run_prediction.sh
sed -i -e 's/\r$//' \
       -e 's|^source ~/venvs/domino/bin/activate$|source ~/venvs/domino_infer/bin/activate|' \
       -e 's|^cd ~/physicsnemo/examples/cfd/external_aerodynamics/domino/src$|cd ~/domino-ahmedml/inference_src|' \
       $D/scripts/run_prediction.sh
chmod +x $D/scripts/run_prediction.sh

echo "=== [7/7] Verifying installation ==="
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
echo "=== Done: local inference environment is ready ==="
