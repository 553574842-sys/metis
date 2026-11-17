# Metis · Creator Knowledge Library for Douyin and Xiaohongshu

[简体中文](README.md) · English

**Keep learning from the creators you follow on Douyin and Xiaohongshu, and apply what you learn to your own projects.**

Build a collection organized by creator. Metis analyzes narration and visuals in videos, as well as text and images in posts. It extracts viewpoints, methods, and examples while keeping links to the original work and supporting evidence.

Search across creators, topics, and formats. When you have a question, find relevant material, compare approaches, and check the original work before applying what you learned.

## Using Metis

1. **Works Library (作品资料库).** Browse works by creator or select **手动导入素材** to import videos, images, or text you have permission to use.
2. **Learning Progress (学习进度).** See which works are queued, processing, or complete. Failed jobs show a reason.
3. **AI Knowledge Center (AI 知识中心).** Search by keyword or question. Follow a citation to review the source and its supporting evidence.

## Windows preview

[Download the Windows x64 portable package](https://github.com/553574842-sys/metis/releases/download/v0.1.0-preview/Metis-Windows-x64.zip)

1. Extract the entire ZIP to a folder you can write to.
2. Double-click `Metis.cmd` and wait for your browser to open.
3. Open a sample work, search for a term from it in the AI Knowledge Center, and follow a result back to its source.

The first launch creates a local library with fictional samples. You can explore them offline without registration or an API key. Keep the launcher window open while using Metis. Closing it stops the service. Start it again from the same folder to access your saved material. The app interface is currently in Chinese.

## Import your own material

In the Windows preview, import local Douyin videos, Xiaohongshu posts, or other material through **手动导入素材**. Add the original URL when you have it. Automatic collection from links and the Personal Brain integration require separate setup.

To analyze your own material with AI, launch Metis once, then close the launcher. Open the generated `.env` file and use `.env.example` to configure your provider, models, and API key. Set `CLOUD_CALLS_ENABLED=true` and restart. Metis sends material to the provider you configure, and that provider's charges apply.

## Local data

Metis stores knowledge and imported material in `data/` and configuration in `.env` and `.env.local`. Close Metis before backing up or moving the folder. The release package contains the app, runtime dependencies, and fictional samples. See `python/LICENSE.txt`, `ffmpeg/LICENSE`, and `ffmpeg/README.txt` for bundled notices.

## Source and terms

You may use and modify Metis and share standalone copies for free under the license terms. You and your team may use Metis as an internal tool, including for paid client work. Resale, integration into a commercial product or service supplied to others, and paid hosting require prior written permission. Third-party components retain their own licenses. [License](LICENSE) · [简体中文译文](LICENSE.zh-CN.md)

### Run from source

Install Python 3.12, Node.js, and FFmpeg and add them to PATH. In Windows PowerShell, run these commands from the repository root:

```powershell
.\scripts\init.ps1 -Demo
.\scripts\start.ps1
```

[Open your local Metis workspace](http://127.0.0.1:8000).

