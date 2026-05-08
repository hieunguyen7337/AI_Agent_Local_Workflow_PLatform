# Research progress: scoping a research problem

## Agentic AI for Video Re-Identification

| Field | Details |
|---|---|
| Student Name | Nhu Hieu Nguyen |
| Student Number | 12194778 |
| Supervisor | Dr Kien Nguyen Thanh |
| Program | Master of Artificial Intelligence |

# Research Background and Literature Analysis

Video-based person re-identification (ReID) aims to match the same person across non-overlapping cameras using image sequences rather than single images. It is important in surveillance, public safety, and multi-camera tracking, but remains difficult because occlusion, pose variation, motion blur, low resolution, lighting changes, and clothing changes can substantially alter appearance across views. Foundational work such as MARS [@zheng2016mars] established video ReID as a large-scale retrieval problem, while later models such as TransReID [@he2021transreid] and TF-CLIP [@yu2024tfclip] showed that transformer-based appearance modelling and text-free CLIP-based representation learning can achieve strong performance on standard benchmarks. However, AG-VPReID [@nguyen2025agvpreid] shows that performance drops dramatically in more difficult aerial-ground settings, where the viewpoint, scale, and resolution shifts are much more severe.

![Camera modalities and viewpoint challenges in the AG-VPReID dataset [@nguyen2025agvpreid]: (a) aerial cameras at 15–120m altitude, (b) indoor and outdoor CCTV cameras, and (c) GoPro cameras from front and side views. The same person appears at vastly different scales, resolutions, and viewpoints across modalities.](figures/reid_challenge.png)

*Figure `fig:reid_challenge`: Camera modalities and viewpoint challenges in the AG-VPReID dataset [@nguyen2025agvpreid]: (a) aerial cameras at 15–120m altitude, (b) indoor and outdoor CCTV cameras, and (c) GoPro cameras from front and side views. The same person appears at vastly different scales, resolutions, and viewpoints across modalities.*

Survey literature such as @saad2024deepvidreidsurvey and @rashidunnabi2025causalvidreidsurvey indicates that most video ReID systems still rely on relatively fixed pipelines built around feature extraction, temporal modelling, and sequence aggregation, and many remain vulnerable to appearance-correlated cues such as clothing, background, and lighting. Related work suggests that complementary evidence may help address this limitation. For example, PromptPAR [@wang2024promptpar] shows that CLIP-based vision-language fusion can improve pedestrian attribute recognition, indicating that semantic attributes may strengthen ReID systems. Within ReID, DeepAgent [@jiao2018deepagent] further suggests that adaptive selection can outperform a single fixed pipeline, although its action space and interpretability remain limited. More recent agentic research points to stronger coordination mechanisms: AOrchestra [@ruan2026aorchestra] shows the value of dynamically orchestrating specialised sub-agents, while 4KAgent [@zuo20254kagent] demonstrates profiling, specialist routing, and iterative execution-reflection in a computer vision pipeline. However, these agentic ideas have not yet been properly translated into video ReID. The key gap, therefore, is between current high-performing but mostly static ReID systems and the need for a framework that can adaptively select, weight, and explain complementary cues such as appearance, attributes, motion, and body-related evidence for each query. This project addresses that gap by investigating an agentic video ReID framework that profiles input conditions, dynamically combines specialist models, and applies refinement only when needed.

**Table `tab:lit_gap_summary`: Summary of representative literature and the gap motivating this project**

| **Study/System** | **Main contribution** | **Limitation relevant to this project** |
|---|---|---|
| MARS [@zheng2016mars] | Established a large-scale benchmark for video-based person ReID | Provides a benchmark foundation only |
| TransReID [@he2021transreid] | Strong transformer-based appearance modelling for ReID | Relies mainly on fixed appearance-based processing |
| TF-CLIP [@yu2024tfclip] | Text-free CLIP-based representation learning for strong video ReID performance | Achieves strong retrieval results, but still within a largely fixed pipeline |
| AG-VPReID [@nguyen2025agvpreid] | Introduces a challenging aerial-ground benchmark that highlights severe viewpoint, scale, and resolution shifts | Identifies the difficulty of the problem, but does not propose an agentic adaptive solution |
| Deep Vid-ReID Survey [@saad2024deepvidreidsurvey] | Summarises mainstream video ReID pipelines and common design patterns | Shows that most existing pipelines remain relatively fixed rather than query-adaptive |
| Causal/In-the-Wild ReID Survey [@rashidunnabi2025causalvidreidsurvey] | Highlights the limits of current systems in realistic settings and their dependence on superficial correlations | Identifies robustness problems, but does not provide an agentic multimodal coordination framework |
| PromptPAR [@wang2024promptpar] | Improves semantic attribute understanding through vision-language prompting | Strengthens attribute cues, but does not provide adaptive multi-specialist coordination |
| DeepAgent [@jiao2018deepagent] | Demonstrates adaptive algorithm selection for person ReID | Limited action space, older experimental setting, and limited interpretability |
| AOrchestra [@ruan2026aorchestra] | Shows dynamic orchestration of specialised sub-agents | General agentic framework, not developed for video ReID or visual retrieval |
| 4KAgent [@zuo20254kagent] | Demonstrates profiling, specialist routing, and iterative execution-reflection in a CV pipeline | Focuses on image super-resolution rather than identity matching or video ReID |

# Research Problem Statement

Video-based person re-identification remains limited in its ability to generalise reliably under real-world conditions such as occlusion, pose variation, low resolution, lighting changes, and cross-camera viewpoint shifts. This project addresses that limitation by investigating whether a query-adaptive, agentic framework can improve identification performance by dynamically selecting and combining complementary sources of evidence, rather than relying on a fixed, appearance-dominant pipeline. This problem is important because, although methods such as TransReID [@he2021transreid] and TF-CLIP [@yu2024tfclip] achieve strong results on standard benchmarks, they largely operate within static architectures and remain vulnerable when conditions become more challenging. AG-VPReID [@nguyen2025agvpreid] further shows that performance drops considerably outside more controlled benchmark settings.

![Proposed agentic video ReID architecture. A Boss Agent (Gemini via ADK) dispatches four specialist agents, Visual (TransReID/ViT), Attribute, Text (SigLIP), and Body Shape (HMR 2.0), whose scores are fused into a final ranked output.](figures/system_arch_slide.png)

*Figure `fig:system_arch`: Proposed agentic video ReID architecture. A Boss Agent (Gemini via ADK) dispatches four specialist agents, Visual (TransReID/ViT), Attribute, Text (SigLIP), and Body Shape (HMR 2.0), whose scores are fused into a final ranked output.*

To address this problem, the project aims to design, implement, and evaluate a multi-agent video ReID system in which specialist modules for appearance, attributes, motion, and body-related cues are coordinated by an agentic controller that profiles input conditions, weights uncertain evidence, and resolves conflicts dynamically, as illustrated in Figure `fig:system_arch`. The novelty of this research lies in introducing agentic orchestration as a query-adaptive decision-making mechanism for video ReID, extending beyond adaptive but domain-limited selection approaches such as DeepAgent [@jiao2018deepagent] and more general orchestration frameworks such as AOrchestra [@ruan2026aorchestra].

The expected outputs include an agentic ReID pipeline, systematic evaluation on benchmark datasets, ablation and robustness analyses, and a research report detailing the methodology, results, and practical implications of the proposed approach.

# Research Questions

This project is guided by two research questions that address complementary aspects of improving video-based person re-identification through an agentic, query-adaptive framework. RQ1 focuses on effectiveness and robustness under difficult visual conditions, while RQ2 focuses on efficiency and practical feasibility. Together, they examine whether an agentic architecture can improve both retrieval quality and operational usefulness beyond static fusion pipelines.

**RQ1: Does a multi-agent, query-adaptive fusion framework improve video-based person re-identification accuracy and robustness over strong fixed-pipeline baselines under challenging conditions such as occlusion, appearance variation, and cross-view shifts?**

This question examines whether dynamically selecting and combining cues such as appearance, semantic attributes, motion, and body-related evidence improves identification performance over a largely fixed architecture. Answering it would produce new knowledge about when and why adaptive coordination is beneficial in video ReID, especially when appearance-only evidence becomes unreliable. It would also help distinguish whether any gains come mainly from using multiple cues or from the agentic mechanism itself, which profiles the query and adjusts specialist weighting. Its limit is that it can show comparative effectiveness and robustness, but not fully explain the causal role of every cue in every setting.

This question can be answered by comparing the proposed agentic system with strong baselines such as TransReID [@he2021transreid] and TF-CLIP [@yu2024tfclip], as well as fixed-fusion variants, on benchmarks including MARS [@zheng2016mars] and AG-VPReID [@nguyen2025agvpreid]. Evaluation can use standard retrieval metrics such as mAP and CMC Rank-1/Rank-5, together with subset analysis under occlusion, viewpoint change, and degraded image quality. Ablation studies can test the contribution of each specialist and of the adaptive controller itself. This is feasible because the datasets, baselines, evaluation protocols, and specialist modelling tools are already available in current literature and open-source ecosystems. The intended outputs are measurable benchmark results, robustness analyses, ablation tables, and qualitative case studies showing when agentic fusion succeeds or fails.

**RQ2: Can an agentic coordinator improve efficiency and interpretability by selectively invoking specialist modules and resolving conflicting evidence without causing a meaningful loss in retrieval accuracy?**

This question examines the practical value of agentic orchestration as a decision-making process rather than only as a fusion strategy. The new knowledge produced would concern whether selective specialist use, confidence-aware routing, and conflict resolution provide a better accuracy-latency trade-off than always-on processing. It would also show whether the coordinator can produce more interpretable decisions by indicating which evidence was trusted, down-weighted, or re-checked for a given query. This contributes to solving the research problem by addressing the need for a system that is not only more robust, but also more efficient and understandable in realistic deployment settings.

This question can be answered by comparing the proposed coordinator against full-feature, always-on baselines and simple static weighting strategies, drawing on adaptive selection ideas from DeepAgent [@jiao2018deepagent] and orchestration logic from AOrchestra [@ruan2026aorchestra]. Evaluation can report retrieval accuracy alongside latency, processed frames per tracklet, and computational cost. Feasibility is strong because selective routing, confidence scoring, and top-$k$ re-ranking can be implemented with contemporary vision models, LLM-based controllers, and standard evaluation pipelines. The tangible outputs are an implemented coordination policy, efficiency-accuracy trade-off results, interpretability examples, and design recommendations for future agentic video ReID systems.

# Research Plan

This research plan outlines a structured strategy that maps each research question to a set of work packages, supported by a clear evaluation methodology, feasibility considerations, and expected outcomes. The plan is designed to ensure that both the effectiveness (RQ1) and efficiency (RQ2) aspects of the proposed agentic video ReID framework are systematically investigated.

## Work Packages

**WP1: Foundations and System Design**

*Aim:* Establish theoretical grounding and define system architecture aligned with the research questions.

*Data:* Literature sources and benchmark dataset specifications
including MARS [@zheng2016mars] and AG-VPReID
[@nguyen2025agvpreid].

*Method:* Conduct literature review on video ReID, multimodal fusion, and agentic AI; design system architecture including specialist modules and agentic coordinator.

*Outputs:* Finalised research questions, system design, evaluation protocol, and initial pipeline skeleton.

**WP2: Specialist Module Development**

*Aim:* Develop independent specialist models corresponding to complementary identity cues.

*Data:* MARS [@zheng2016mars], Market-1501
[@zheng2015market1501], and AG-VPReID
[@nguyen2025agvpreid] datasets.

*Method:* Implement or fine-tune models for appearance
(e.g., TransReID [@he2021transreid]), attributes
(classification networks), and multimodal/text representations
(e.g., text-free CLIP-based embeddings [@yu2024tfclip]),
and optionally body-related cues.

*Outputs:* Functional specialist modules with validated feature extraction and baseline performance.

**WP3: Agentic Coordination and Integration**

*Aim:* Address RQ1 by integrating specialists into a query-adaptive framework.

*Data:* Benchmark datasets with query-gallery splits.

*Method:* Implement an agentic coordinator that profiles input conditions, dynamically weights specialist outputs, and resolves conflicting evidence through adaptive decision-making.

*Outputs:* Complete agentic ReID pipeline capable of adaptive fusion and reasoning.

**WP4: Evaluation and Analysis**

*Aim:* Address both RQ1 and RQ2 through systematic experimentation.

*Data:* MARS [@zheng2016mars] and AG-VPReID
[@nguyen2025agvpreid] datasets, including subsets with
occlusion and viewpoint variation.

*Method:* Compare against baselines including single-modality models, fixed fusion, and learned fusion; perform ablation studies and robustness analysis. Evaluate efficiency through selective specialist invocation.

*Outputs:* Quantitative results, ablation studies, qualitative case analysis, and efficiency-accuracy trade-off evaluation.

**WP5: Documentation and Dissemination**

*Aim:* Consolidate findings into formal outputs.

*Data:* Experimental results and system outputs.

*Method:* Compile research findings into a structured thesis and report; document system design, evaluation, and insights.

*Outputs:* Final thesis, research report, and potential publication-ready manuscript.

## Evaluation Strategy

The evaluation will follow standard person ReID protocols using query-gallery retrieval tasks. Performance will be measured using mean Average Precision (mAP) and Cumulative Matching Characteristic (CMC) metrics (Rank-1, Rank-5). Baselines include strong appearance-based models (e.g., TransReID), multimodal models (e.g., TF-CLIP), and fixed or learned fusion strategies.

To evaluate robustness (RQ1), experiments will include subset analysis under occlusion, low resolution, and cross-view variation. To evaluate efficiency (RQ2), additional metrics such as inference latency, number of specialist calls, and computational cost (e.g., FLOPs or processing time) will be measured. The evaluation protocol ensures fair comparison by using consistent datasets, splits, and feature extraction pipelines across all methods.

## Ethics and Data Governance

This project uses publicly available benchmark datasets that contain pedestrian imagery. Ethical considerations include privacy, consent, and responsible use of surveillance-related technologies. The research will adhere to dataset usage agreements and academic integrity guidelines. No attempt will be made to identify real individuals, and all experiments will focus on technical evaluation rather than deployment. Data will be stored securely and used only for research purposes.

## Timeline and Milestones

The project is structured over a semester timeline:
- Weeks 1–4: Literature review, problem scoping, and system design (WP1)
- Weeks 5–7: Specialist module development (WP2)
- Weeks 8–9: Agentic integration and prototype implementation (WP3)
- Weeks 10–12: Experimental evaluation and analysis (WP4)
- Weeks 13 onward: Thesis writing and finalisation (WP5)

Key milestones include completion of the system prototype, baseline evaluation results, and final experimental analysis.

## Expected Contributions and Deliverables

This research is expected to contribute a novel agentic framework for video-based person re-identification that improves robustness and adaptability compared to static pipelines. Key contributions include: (1) a query-adaptive multi-agent ReID architecture, (2) empirical analysis of when adaptive fusion improves performance, and (3) insights into efficiency-accuracy trade-offs in agentic systems.

The deliverables include a working prototype system, experimental evaluation results, ablation and robustness studies, and a comprehensive research thesis documenting the methodology and findings. These outputs are feasible within the project scope due to the availability of established datasets, baseline models, and implementation frameworks.

## References

> References are stored in `references.bib` in the original LaTeX project.

# Reference Architecture Diagrams

This appendix reproduces the architecture diagrams of the three
prior works most directly relevant to the proposed system, as
discussed in Section `sec:background`. These figures provide
visual context for understanding how the proposed agentic ReID
framework extends and departs from each of these designs.

![TF-CLIP [@yu2024tfclip]: CLIP-Memory tokens replace text supervision; Temporal Memory Diffusion propagates them across frames for text-free video ReID.](figures/tfclip_arch.png)

*Figure `fig:tfclip_arch`: TF-CLIP [@yu2024tfclip]: CLIP-Memory tokens replace text supervision; Temporal Memory Diffusion propagates them across frames for text-free video ReID.*

![DeepAgent [@jiao2018deepagent]: an RL agent selects augmentation strategies and feature extractors (ResNet-50/DenseNet-121) per query, achieving 91.1% Rank-1 on Market-1501.](figures/deepagent_arch.png)

*Figure `fig:deepagent_arch`: DeepAgent [@jiao2018deepagent]: an RL agent selects augmentation strategies and feature extractors (ResNet-50/DenseNet-121) per query, achieving 91.1% Rank-1 on Market-1501.*

![AOrchestra [@ruan2026aorchestra]: a central orchestrator configures dynamic sub-agents via $(M, T, I, C)$ tuples (a–b), with self-supervised training and in-context optimisation (c–d).](figures/aorchestra_arch.png)

*Figure `fig:aorchestra_arch`: AOrchestra [@ruan2026aorchestra]: a central orchestrator configures dynamic sub-agents via $(M, T, I, C)$ tuples (a–b), with self-supervised training and in-context optimisation (c–d).*
