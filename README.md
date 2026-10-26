x-dsh
---

## 1 QUICK START

### 1.1 编译

```sh
./build.sh
```

### 1.2 配置文件

```sh
cp .env.example .env
```

> 填写或修改对应配置项目 尤其`DEEPSEEK_API_KEY`和`X_HARNESS_TOKEN`

### 1.3 启动

```sh
./run.sh
```

### 1.4 交互

默认会自动拉起本地服务或者手动访问本地浏览器

```sh
http://127.0.0.1:8080/
```

## 2 SAMPLES

### 2.1 设置工作区

![](./doc/img/1792993791.png)

### 2.2 web会话输入

- 显式指定输出路径 `调用split_requirement_document工具转换02-跨章节合并.docx outputDir设为outputs 完成后告诉我 treeDir`

- 使用默认输出路径 `调用split_requirement_document工具转换02-跨章节合并.docx`

![](./doc/img/1792994052.png)

### 2.3 输出结构

每次任务创建独立目录，不会覆盖已有结果：

```text
<outputRoot>/<文档名>-<时间戳>/
├── document.md                 # 转换后的 Markdown
├── document.normalized.md      # 统一章节编号后的 Markdown
├── convert_manifest.json       # 图片、待 OCR 页、告警
├── source/                     # 源文件副本
├── assets/                     # 抽取出的原始图片
└── requirements/
    ├── chunks.json             # 语义分块结果
    ├── split_report.json       # 拆分与校验报告
    ├── assets_report.json      # 图片跟随/悬空引用报告
    ├── report.json             # 最终汇总（需求/合并/图片/OCR/告警）
    ├── requirement_relations.json
    └── <功能目录>/
        ├── <功能>.md           # 条目化需求
        └── assets/             # 跟随该需求的图片
```

拆分后 Markdown 中的图片引用会重写为需求目录内的相对路径，例如 `assets/img_001.png`。

图片型 PDF 默认使用本地 RapidOCR，在 Markdown 中写入 `<!-- OCR page N provider=... -->` 标记，并在 `convert_manifest.json` 的 `ocr_pages` 中记录识别状态。可通过 `XAGENTS_OCR_PROVIDER=vision` 切换到 OpenAI 兼容的视觉模型。