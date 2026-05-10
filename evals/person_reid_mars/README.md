# MARS person ReID eval helpers

## Partition builder (`build_partition.py`)

Builds a **tracklet-level** eval slice aligned with the official MARS test split (Torchreid convention):

- **Queries:** indices listed in `info/query_IDX.mat` (indexed against `tracks_test_info`).
- **Gallery:** every other row of `tracks_test_info` (official complement of queries).
- **Representative image:** middle frame of each tracklet, `(num_frames - 1) // 2`.

Default output layout matches Market-style partitions: `query/`, `bounding_box_test/`, `dataset.yaml`, and `partition_stats.json`.

### Requirements

- SciPy (`scipy.io.loadmat`)
- Local MARS root under e.g. `dataset/mars/` with `bbox_test/` and `info/` (`dataset.md`).
- Run from repo root so `backend.repo_root` resolves.

### Example

```bash
python evals/person_reid_mars/build_partition.py \
  --mars-root dataset/mars \
  --output-dir evals/person_reid_mars/partition_100q_1000g \
  --n-queries 100 \
  --gallery-size 1000 \
  --seed 42
```

Embedding / retrieval DB generation is **not** part of this script (planned separately).
