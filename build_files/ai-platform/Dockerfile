# Single source of platform image versions, maintained by Dependabot.
# This file is a dependency catalog parsed by bluefin-ai-platform; it is not built.
# Keep each alias stable: platform.json and runtime profiles use image:<alias>.
# Rolling tags (server-vulkan/latest) are still immutable because their digest is pinned.
FROM docker.io/maximhq/bifrost:v2.5.1@sha256:5ec9873acaf1687556b6e4dd854b92b539d9debf87a5a683b66338648b84d3c8 AS bifrost
FROM ghcr.io/open-webui/open-webui:v0.13.4@sha256:d5168bfbc28609eff5bea1b438dd864cf64d185cf6582c02244865e18385b901 AS webui
FROM docker.io/library/nginx:1.31.8-alpine@sha256:fed7e14be83cbd2661a75cdc5b872d936cf155d46c327084d30b8df19ae88d72 AS edge
FROM ghcr.io/ggml-org/llama.cpp:server-vulkan@sha256:8af21639d085883fe5147a25a2ee29ce38302d950faa7e42355556d9f9a5af6c AS llamacpp
FROM quay.io/ramalama/ramalama:0.26@sha256:9a8362e0a9a95df67d8e85cd8197aa25ce7aa3530e9d954b85b76cda152dd8d1 AS ramalama
FROM ghcr.io/niko1221/strata:0.2.16-rocm-gfx1151@sha256:d144c4f86b1081fa3f2e947d9e7c53654d130113aa458c15598a6e012dc34383 AS strata
FROM docker.io/rocm/vllm:latest@sha256:e0ea9a10b729573ea8598bdc073bda0ea382fe1037eec7c0761b6fa7f46baaf4 AS vllm
