"""Make a same-scale face board from the left portrait of each human character sheet."""
import argparse
from math import ceil
from pathlib import Path

import cv2
import numpy as np


def make_board(files, output):
    model = Path(__file__).resolve().parents[1] / "models" / "yunet.onnx"
    detector = cv2.FaceDetectorYN.create(str(model), "", (320, 320), 0.8)
    columns = min(5, len(files))
    board = np.full((ceil(len(files) / columns) * 190, columns * 156, 3), 248, np.uint8)
    for i, path in enumerate(files):
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"无法读取设定图 {path}")
        portrait = image[:, :int(image.shape[1] * 0.40)].copy()
        detector.setInputSize((portrait.shape[1], portrait.shape[0]))
        _, faces = detector.detect(portrait)
        if faces is None:
            raise ValueError(f"需要人工裁切正面特写 {path}")
        face = max(faces, key=lambda f: f[2] * f[3])
        eyes = face[4:8].reshape(2, 2)
        left, right = sorted(eyes, key=lambda eye: eye[0])
        dx, dy = right - left
        distance = np.hypot(dx, dy)
        if distance < 8:
            raise ValueError(f"眼部间距太小，需人工复核 {path}")
        center = (left + right) / 2
        transform = cv2.getRotationMatrix2D(tuple(map(float, center)),
                                            float(np.degrees(np.arctan2(dy, dx))), 53 / distance)
        transform[:, 2] += np.array([78, 61]) - center
        crop = cv2.warpAffine(portrait, transform, (156, 164), borderMode=cv2.BORDER_REPLICATE)
        x, y = (i % columns) * 156, (i // columns) * 190
        board[y:y+164, x:x+156] = crop
        cv2.putText(board, path.stem, (x+4, y+183), cv2.FONT_HERSHEY_SIMPLEX, .52, (40, 40, 40), 1)
    if not cv2.imwrite(str(output), board):
        raise OSError(f"无法保存正脸对照 {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sheets", type=Path, nargs="+")
    args = parser.parse_args()
    make_board(args.sheets, args.output)
