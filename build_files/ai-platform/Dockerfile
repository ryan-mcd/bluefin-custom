# Single source of platform image versions, maintained by Dependabot.
# This file is a dependency catalog parsed by bluefin-ai-platform; it is not built.
# Keep each alias stable: platform.json and runtime profiles use image:<alias>.
# Rolling tags (server-vulkan/latest) are still immutable because their digest is pinned.
FROM docker.io/maximhq/bifrost:v2.5.1@sha256:5ec9873acaf1687556b6e4dd854b92b539d9debf87a5a683b66338648b84d3c8 AS bifrost
FROM ghcr.io/open-webui/open-webui:v0.13.4@sha256:d5168bfbc28609eff5bea1b438dd864cf64d185cf6582c02244865e18385b901 AS webui
FROM docker.io/library/nginx:1.31.7-alpine@sha256:892bb2152464f0c5d8972593a5f5afaf8054552580b699d95f8ff4af08c2d33d AS edge
FROM ghcr.io/ggml-org/llama.cpp:server-vulkan@sha256:f885c1137a997b318eede280b76b3f35cb84b757c218f41480a33be2b129b8a9 AS llamacpp
FROM quay.io/ramalama/ramalama:0.26@sha256:9a8362e0a9a95df67d8e85cd8197aa25ce7aa3530e9d954b85b76cda152dd8d1 AS ramalama
FROM ghcr.io/niko1221/strata:0.2.13-rocm-gfx1151@sha256:3c96aa87d636230d2c8e804f60f93f6b630e9eb0c137cf722de1482d1fb8f92f AS strata
FROM docker.io/rocm/vllm:latest@sha256:e0ea9a10b729573ea8598bdc073bda0ea382fe1037eec7c0761b6fa7f46baaf4 AS vllm
