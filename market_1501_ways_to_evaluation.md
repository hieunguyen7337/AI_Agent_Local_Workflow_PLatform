# Running Evaluation on Market-1501 Dataset

Given your work on person ReID with ViT models, here's a comprehensive guide to the best evaluation approaches:

## Standard Evaluation Metrics

Market-1501 uses two standard metrics:
- **mAP (mean Average Precision)** — overall ranking quality
- **CMC Rank-1 / Rank-5 / Rank-10** — identification accuracy at top-k retrievals

---

## Option 1: Using `torchreid` (Recommended)

The most battle-tested library for Market-1501 eval:

```bash
pip install torchreid
```

```python
import torchreid

# Build data manager (handles query/gallery splits automatically)
datamanager = torchreid.data.ImageDataManager(
    root='path/to/dataset',
    sources='market1501',
    targets='market1501',
    height=256,
    width=128,
    batch_size_train=32,
    batch_size_test=100,
    transforms=['random_flip', 'pad', 'random_crop', 'normalize']
)

# Build model
model = torchreid.models.build_model(
    name='resnet50',
    num_classes=datamanager.num_train_pids,
    pretrained=True
)

# Build engine
engine = torchreid.engine.ImageSoftmaxEngine(
    datamanager, model,
    optimizer=torchreid.optim.build_optimizer(model, optim='adam', lr=0.0003),
    scheduler=torchreid.optim.build_lr_scheduler(optimizer, lr_scheduler='single_step')
)

# Run test only
engine.run(
    test_only=True,
    dist_metric='euclidean'  # or 'cosine'
)
```

---

## Option 2: Custom Evaluation Loop (for ViT/custom models)

If you're evaluating your own ViT-based ReID model:

```python
import torch
import numpy as np
from torchreid.metrics import evaluate_rank

def extract_features(model, dataloader, device):
    model.eval()
    feats, pids, camids = [], [], []
    with torch.no_grad():
        for imgs, batch_pids, batch_camids, _ in dataloader:
            imgs = imgs.to(device)
            f = model(imgs)  # your ViT forward pass
            feats.append(f.cpu())
            pids.extend(batch_pids.numpy())
            camids.extend(batch_camids.numpy())
    return torch.cat(feats), np.array(pids), np.array(camids)

# Extract query and gallery features
qf, q_pids, q_camids = extract_features(model, query_loader, device)
gf, g_pids, g_camids = extract_features(model, gallery_loader, device)

# Compute distance matrix (cosine recommended for ViT)
distmat = 1 - torch.mm(
    torch.nn.functional.normalize(qf, dim=1),
    torch.nn.functional.normalize(gf, dim=1).t()
).numpy()

# Evaluate
cmc, mAP = evaluate_rank(distmat, q_pids, g_pids, q_camids, g_camids)
print(f"mAP: {mAP:.2%}")
print(f"Rank-1: {cmc[0]:.2%}, Rank-5: {cmc[4]:.2%}, Rank-10: {cmc[9]:.2%}")
```

---

## Option 3: Using `fast-reid` (State-of-the-art framework)

Best if you want SOTA baselines and support for ViT backbones:

```bash
git clone https://github.com/JDAI-CV/fast-reid
cd fast-reid
pip install -r requirements.txt
```

```bash
# Evaluate a pretrained model
python tools/train_net.py \
  --config-file configs/Market1501/bagtricks_R50.yml \
  --eval-only \
  MODEL.WEIGHTS /path/to/model.pth \
  OUTPUT_DIR logs/market1501_eval
```

fast-reid supports ViT configs out of the box under `configs/Market1501/`.

---

## Market-1501 Dataset Structure

Make sure your dataset is organized correctly:

```
Market-1501/
├── bounding_box_train/   # 12,936 images, 751 identities
├── bounding_box_test/    # gallery set, 19,732 images, 750 identities
├── query/                # 3,368 query images
└── gt_bbox/              # ground truth (optional)
```

---

## Key Evaluation Protocol Notes

| Point | Detail |
|---|---|
| **Camera same-ID filtering** | Remove gallery images with same cam ID **and** same PID as query (junk images) |
| **Distance metric** | Cosine is generally better for ViT; Euclidean for CNN |
| **Re-ranking** | `k-reciprocal re-ranking` (Zhong et al.) can boost mAP by ~5–8% |
| **Multi-query** | Average features from multiple query images of same ID |

### Re-ranking (optional but impactful):
```python
from torchreid.utils.rerank import re_ranking

distmat = re_ranking(qf, gf, k1=20, k2=6, lambda_value=0.3)
cmc, mAP = evaluate_rank(distmat, q_pids, g_pids, q_camids, g_camids)
```

---

## Recommended Approach for Your ViT ReID Work

Since you're working with ViT models and Google ADK agents, I'd suggest:
1. **fast-reid** for structured baseline comparison
2. **Custom loop with `torchreid.metrics.evaluate_rank`** for your own model weights
3. Always report **both mAP and Rank-1** — they capture different aspects (ranking quality vs. top-1 accuracy)

Want me to help set up a full evaluation script tailored to your specific ViT/PAR model pipeline?