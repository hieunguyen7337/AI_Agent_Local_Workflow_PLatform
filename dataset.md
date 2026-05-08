# Dataset Notes

This repo keeps heavyweight datasets local and out of Git. The root `dataset/` directory is ignored by `.gitignore`; workflow specs and eval builders should reference local paths, while generated partitions, indexes, and SQLite databases remain under ignored eval artifact paths.

## Current Local Dataset Folder

At the time this note was written, the only dataset present under `dataset/` is:

```text
dataset/
`-- Market-1501-v15.09.15/
    |-- bounding_box_test/   19,732 jpg files
    |-- bounding_box_train/  12,936 jpg files
    |-- gt_bbox/             25,259 jpg files
    |-- gt_query/             6,736 files
    |-- query/                3,368 jpg files
    `-- readme.txt
```

The local `readme.txt` describes Market-1501 as a six-camera person re-identification dataset collected at Tsinghua University, with 1,501 annotated identities, 751 training identities, 750 testing identities, and 3,368 query images. It also states the dataset is for research use only and should not be redistributed or used commercially.

## Market-1501 Download Sources

Official project page:

- <https://zheng-lab-anu.github.io/Project/project_reid.html>

Dataset package links listed by the official page:

- Google Drive: <https://drive.google.com/file/d/0B8-rUzbwVRk0c054eEozWG9COHM/view?resourcekey=0-8nyl7K9_x37HlQm34MmrYQ&usp=sharing>
- Baidu Disk: <https://pan.baidu.com/s/1ntIi2Op>
- Direct server mirror: <http://188.138.127.15:81/Datasets/Market-1501-v15.09.15.zip>

Some mirrors and tooling, including Torchreid, reference the same direct server zip URL and the original Market-1501 project page. If one link is unavailable, try another official-page link before using third-party mirrors.

## Repo Usage

The local person re-identification eval scripts expect the extracted dataset root as `Market-1501-v15.09.15`, for example:

```powershell
.\.venv\Scripts\python evals\person_reid_market1501\build_dataset.py `
  --market1501-root dataset\Market-1501-v15.09.15 `
  --n-queries 100 `
  --gallery-size 500 `
  --seed 42
```

Current related repo paths:

- `workflows/person_reid_market1501.yaml`
- `workflows/person_reid_market1501_eval.yaml`
- `evals/person_reid_market1501/dataset_eval.yaml`
- `evals/person_reid_market1501/build_dataset.py`
- `evals/person_reid_market1501/build_partition.py`
- `evals/person_reid_market1501/build_description_dbs.py`

Generated eval inputs and databases are intentionally ignored, including `evals/person_reid_market1501/dataset.yaml`, `gallery_index/`, `gallery_db/`, `embedding_db/`, `description_db/`, and partition folders.

## Citation

The official project page requests citing:

```bibtex
@inproceedings{zheng2015scalable,
  title={Scalable Person Re-identification: A Benchmark},
  author={Zheng, Liang and Shen, Liyue and Tian, Lu and Wang, Shengjin and Wang, Jingdong and Tian, Qi},
  booktitle={Computer Vision, IEEE International Conference on},
  year={2015}
}
```
