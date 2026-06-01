#!/usr/bin/env python3
"""
团购视频生成器 — FastAPI 后端 API 服务

提供 REST API + WebSocket 实时进度推送，集成 video_maker.py 核心模块。

启动方式:
  cd /Users/xhh/Documents/video-maker && python3.11 web/server.py

默认端口: 8000
"""

import asyncio
import json
import logging
import os
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# ── 将项目根目录加入 sys.path ──────────────────────────
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import video_maker
import publish_utils

# ── 日志配置 ──────────────────────────────────────────
logger = logging.getLogger("video_maker_web")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


# ══════════════════════════════════════════════════════
# Pydantic 请求/响应模型
# ══════════════════════════════════════════════════════

class GenerateRequest(BaseModel):
    """单视频生成请求"""
    mode: str = Field("topic", description="生成模式: topic|script|text")
    topic: Optional[str] = Field(None, description="主题（mode=topic 时必填）")
    script_path: Optional[str] = Field(None, description="脚本文件路径（mode=script 时必填）")
    text: Optional[str] = Field(None, description="自定义文案内容（mode=text 时必填）")
    style: str = Field("团购种草", description="文案风格")
    template: Optional[str] = Field(None, description="商家模板 YAML 路径")
    tts_engine: Optional[str] = Field(None, description="TTS 引擎: edge_tts|mimo_tts")
    tts_voice: Optional[str] = Field(None, description="TTS 音色")
    subtitle_style: Optional[int] = Field(None, description="字幕样式 1-8")
    subtitle_animation: Optional[str] = Field(None, description="字幕动画: none|fade|slide|typewriter")
    subtitle_position: Optional[str] = Field(None, description="字幕位置: top|center|bottom")
    subtitle_margin: Optional[int] = Field(None, description="字幕边距（像素）")
    images: List[str] = Field(default_factory=lambda: ["素材"], description="图片路径列表")
    output_dir: Optional[str] = Field(None, description="自定义输出目录")
    title: Optional[str] = Field(None, description="视频标题")
    count: Optional[int] = Field(None, description="文案句数（mode=topic 时，6/8/10/12）")
    video_count: int = Field(1, ge=1, le=100, description="生成视频数量（1-100）")
    # 封面相关
    cover: bool = Field(False, description="是否生成封面图")
    cover_text: Optional[str] = Field(None, description="封面标题文字，留空使用视频标题")
    cover_subtitle: Optional[str] = Field("", description="封面副标题")
    cover_duration: Optional[float] = Field(None, description="封面展示时长（秒），默认 3.0")


class BatchTaskItem(BaseModel):
    """批量任务中的单个项"""
    topic: Optional[str] = None
    script_path: Optional[str] = None
    text: Optional[str] = None
    style: str = "团购种草"
    template: Optional[str] = None
    tts_engine: Optional[str] = None
    tts_voice: Optional[str] = None
    subtitle_style: Optional[int] = None
    subtitle_animation: Optional[str] = None
    subtitle_position: Optional[str] = None
    subtitle_margin: Optional[int] = None
    title: Optional[str] = None
    # 封面相关
    cover: bool = False
    cover_text: Optional[str] = None
    cover_subtitle: Optional[str] = ""
    cover_duration: Optional[float] = None


class BatchRequest(BaseModel):
    """批量生成请求"""
    tasks: List[BatchTaskItem] = Field(..., min_length=1, description="任务列表")
    images: List[str] = Field(default_factory=lambda: ["素材"], description="共享图片路径列表")
    output_dir: Optional[str] = Field(None, description="自定义输出目录")


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""
    api: Optional[Dict[str, Any]] = None
    video: Optional[Dict[str, Any]] = None
    audio: Optional[Dict[str, Any]] = None
    batch: Optional[Dict[str, Any]] = None
    tts: Optional[Dict[str, Any]] = None


class ScheduleRequest(BaseModel):
    """定时/周期任务请求"""
    request_payload: GenerateRequest = Field(..., description="生成视频的原始请求")
    start_time: Optional[str] = Field(None, description="开始时间，ISO格式，留空则立即开始")
    interval_hours: Optional[float] = Field(None, description="周期执行的间隔小时数")
    videos_per_interval: int = Field(1, ge=1, le=100, description="每次周期执行生成的视频数")
    is_infinite: bool = Field(False, description="是否无限次执行")
    max_runs: Optional[int] = Field(None, description="最大执行次数")

class SchedulePublishRequest(BaseModel):
    """定时发布请求"""
    task_ids: List[str]
    interval_hours: float = 2.0
    start_time: Optional[str] = None # HH:MM or ISO



# ══════════════════════════════════════════════════════
# 任务管理
# ══════════════════════════════════════════════════════

class TaskInfo:
    """任务状态信息"""

    def __init__(
        self,
        task_id: str,
        params: dict,
        batch_id: Optional[str] = None,
    ):
        self.id = task_id
        self.batch_id = batch_id
        self.status = "pending"  # pending | running | completed | failed | cancelled
        self.progress = 0
        self.step = ""
        self.params = params
        self.output_file: Optional[str] = None
        self.error: Optional[str] = None
        self.created_at = datetime.now().isoformat()
        self.completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "status": self.status,
            "progress": self.progress,
            "step": self.step,
            "params": self.params,
            "output_file": self.output_file,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class TaskManager:
    """内存任务管理器"""

    def __init__(self):
        self.tasks: Dict[str, TaskInfo] = {}
        self._lock = asyncio.Lock()

    def create_task(self, params: dict, batch_id: Optional[str] = None) -> TaskInfo:
        task_id = uuid.uuid4().hex[:12]
        task = TaskInfo(task_id=task_id, params=params, batch_id=batch_id)
        self.tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        return self.tasks.get(task_id)

    def list_tasks(
        self,
        status: Optional[str] = None,
        batch_id: Optional[str] = None,
    ) -> List[TaskInfo]:
        result = list(self.tasks.values())
        if status:
            result = [t for t in result if t.status == status]
        if batch_id:
            result = [t for t in result if t.batch_id == batch_id]
        # 按创建时间倒序
        result.sort(key=lambda t: t.created_at, reverse=True)
        return result

    def delete_task(self, task_id: str) -> bool:
        if task_id in self.tasks:
            del self.tasks[task_id]
            return True
        return False


# ══════════════════════════════════════════════════════
# 调度管理 (ScheduleManager)
# ══════════════════════════════════════════════════════

class ScheduleManager:
    """管理定时/周期任务"""

    def __init__(self, data_file: str = "schedules.json"):
        self.data_file = Path(PROJECT_ROOT) / data_file
        self.schedules: Dict[str, dict] = {}
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            self.scheduler = AsyncIOScheduler()
            self.has_apscheduler = True
        except ImportError:
            logger.warning("未安装 apscheduler，定时任务功能不可用")
            self.has_apscheduler = False

    def load(self):
        if not self.has_apscheduler: return
        if self.data_file.exists():
            try:
                data = json.loads(self.data_file.read_text(encoding="utf-8"))
                for job_id, sched_data in data.items():
                    self.schedules[job_id] = sched_data
                    self._resume_job(job_id, sched_data)
                logger.info(f"加载了 {len(self.schedules)} 个定时任务")
            except Exception as e:
                logger.error(f"加载 schedules.json 失败: {e}")

    def save(self):
        try:
            self.data_file.write_text(json.dumps(self.schedules, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.error(f"保存 schedules.json 失败: {e}")

    def start(self):
        if self.has_apscheduler and not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self):
        if self.has_apscheduler and self.scheduler.running:
            self.scheduler.shutdown()

    def _execute_scheduled_task(self, job_id: str):
        if job_id not in self.schedules: return
        sched_data = self.schedules[job_id]
        
        if not sched_data.get("is_infinite") and sched_data.get("max_runs"):
            runs = sched_data.get("runs_completed", 0)
            if runs >= sched_data["max_runs"]:
                self.delete_schedule(job_id)
                return

        logger.info(f"触发定时任务 {job_id}")
        payload_dict = sched_data["request_payload"]
        try:
            req = GenerateRequest(**payload_dict)
            req.video_count = sched_data.get("videos_per_interval", 1)
            
            loop = asyncio.get_event_loop()
            loop.create_task(self._submit_to_task_manager(req))
        except Exception as e:
            logger.error(f"执行定时任务 {job_id} 时出错: {e}")

        sched_data["runs_completed"] = sched_data.get("runs_completed", 0) + 1
        self.save()

        if not sched_data.get("is_infinite") and sched_data.get("max_runs"):
            if sched_data["runs_completed"] >= sched_data["max_runs"]:
                self.delete_schedule(job_id)

    async def _submit_to_task_manager(self, req: GenerateRequest):
        base_params = {
            "mode": req.mode,
            "topic": req.topic,
            "script_path": req.script_path,
            "text": req.text,
            "style": req.style,
            "template": req.template,
            "tts_engine": req.tts_engine,
            "tts_voice": req.tts_voice,
            "subtitle_style": req.subtitle_style,
            "subtitle_animation": req.subtitle_animation,
            "subtitle_position": req.subtitle_position,
            "subtitle_margin": req.subtitle_margin,
            "images": req.images,
            "output_dir": req.output_dir,
            "title": req.title,
            "count": req.count,
            "cover": req.cover,
            "cover_text": req.cover_text,
            "cover_subtitle": req.cover_subtitle,
            "cover_duration": req.cover_duration,
        }
        
        config = _get_task_config(req.tts_engine, req.tts_voice, req.subtitle_style,
                                  req.subtitle_animation, req.cover_duration,
                                  req.subtitle_position, req.subtitle_margin)

        if req.video_count == 1:
            task = task_manager.create_task(params=base_params)
            asyncio.create_task(run_generate_task(task, config))
        else:
            batch_id = uuid.uuid4().hex[:12]
            created_tasks = []
            for i in range(req.video_count):
                params = dict(base_params)
                if req.title:
                    params["title"] = f"{req.title}_{i+1:02d}"
                elif req.topic:
                    params["title"] = f"{req.topic}_{i+1:02d}"
                else:
                    params["title"] = f"视频_{i+1:02d}"
                params["batch_index"] = i + 1
                params["batch_total"] = req.video_count
                task = task_manager.create_task(params=params, batch_id=batch_id)
                created_tasks.append(task)
            
            asyncio.create_task(run_batch_tasks(batch_id, created_tasks, config))

    def _resume_job(self, job_id: str, sched_data: dict):
        if not self.has_apscheduler: return
        interval = sched_data.get("interval_hours")
        start_time_str = sched_data.get("start_time")
        start_date = datetime.fromisoformat(start_time_str) if start_time_str else None

        if interval:
            from apscheduler.triggers.interval import IntervalTrigger
            trigger = IntervalTrigger(hours=interval, start_date=start_date)
            self.scheduler.add_job(self._execute_scheduled_task, trigger, args=[job_id], id=job_id, replace_existing=True)
        else:
            from apscheduler.triggers.date import DateTrigger
            if start_date:
                self.scheduler.add_job(self._execute_scheduled_task, DateTrigger(run_date=start_date), args=[job_id], id=job_id, replace_existing=True)
            else:
                self.scheduler.add_job(self._execute_scheduled_task, args=[job_id], id=job_id, replace_existing=True)

    def add_schedule(self, req: ScheduleRequest) -> str:
        if not self.has_apscheduler:
            raise Exception("APScheduler 未安装")
            
        job_id = uuid.uuid4().hex[:12]
        sched_data = {
            "id": job_id,
            "request_payload": req.request_payload.dict(),
            "start_time": req.start_time,
            "interval_hours": req.interval_hours,
            "videos_per_interval": req.videos_per_interval,
            "is_infinite": req.is_infinite,
            "max_runs": req.max_runs,
            "runs_completed": 0,
            "created_at": datetime.now().isoformat()
        }
        
        self.schedules[job_id] = sched_data
        self._resume_job(job_id, sched_data)
        self.save()
        return job_id

    def delete_schedule(self, job_id: str):
        if job_id in self.schedules:
            del self.schedules[job_id]
            self.save()
        if self.has_apscheduler and self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    def get_all(self):
        return list(self.schedules.values())



# ══════════════════════════════════════════════════════
# WebSocket 连接管理
# ══════════════════════════════════════════════════════

class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket 客户端连接，当前连接数: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket 客户端断开，当前连接数: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """向所有连接广播消息"""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)

    async def send_to(self, websocket: WebSocket, message: dict):
        """向单个连接发送消息"""
        try:
            await websocket.send_json(message)
        except Exception:
            self.disconnect(websocket)


# ══════════════════════════════════════════════════════
# 全局状态
# ══════════════════════════════════════════════════════

app = FastAPI(
    title="团购视频生成器 API",
    description="Web 前端后端 API，集成 video_maker.py 视频生成能力",
    version="1.0.0",
)

@app.on_event("startup")
async def startup_event():
    schedule_manager.load()
    schedule_manager.start()
    publish_utils.init_scheduler()

@app.on_event("shutdown")
async def shutdown_event():
    schedule_manager.shutdown()

task_manager = TaskManager()
schedule_manager = ScheduleManager()

ws_manager = ConnectionManager()

# 全局并发限制：最多同时运行 N 个视频生成任务，防止资源撑爆
MAX_CONCURRENT_TASKS = 3
_task_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

# 加载配置
runtime_config: Dict[str, Any] = video_maker.load_config()

# 项目根目录（用于解析相对路径）
BASE_DIR = PROJECT_ROOT


def resolve_path(path_str: str) -> str:
    """将相对路径解析为绝对路径"""
    p = Path(path_str)
    if p.is_absolute():
        return str(p)
    return str(Path(BASE_DIR) / p)


# ══════════════════════════════════════════════════════
# CORS 中间件
# ══════════════════════════════════════════════════════

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════
# 视频生成任务执行
# ══════════════════════════════════════════════════════

async def _run_generate_task_inner(task: TaskInfo, config: dict):
    """视频生成核心逻辑（不含并发控制）"""

    try:
        task.status = "running"
        task.step = "初始化"
        task.progress = 0
        await ws_manager.broadcast({
            "type": "progress",
            "task_id": task.id,
            "step": task.step,
            "percent": task.progress,
        })

        params = task.params
        mode = params.get("mode", "topic")
        images_dirs = params.get("images", ["素材"])
        # 搜索有效的素材目录（支持素材库目录）
        images_dir = None
        for d in images_dirs:
            resolved = resolve_path(d)
            if Path(resolved).exists():
                images_dir = resolved
                break
        if not images_dir:
            # 降级：依次尝试素材库/图片、素材
            for fallback in ["素材库/图片", "素材"]:
                resolved = resolve_path(fallback)
                if Path(resolved).exists():
                    images_dir = resolved
                    break
        if not images_dir:
            images_dir = resolve_path("素材")

        # 优先使用请求中指定的 output_dir，否则使用配置中的
        if params.get("output_dir"):
            output_dir = params["output_dir"]
        else:
            output_dir = config["batch"]["output_dir"]
        output_dir = resolve_path(output_dir)

        # 构建生成参数
        gen_kwargs: Dict[str, Any] = {
            "images_dir": images_dir,
            "output_dir": output_dir,
            "config": config,
        }

        # 根据 mode 设置文案来源
        if mode == "topic":
            topic = params.get("topic")
            if not topic:
                raise ValueError("mode=topic 时必须提供 topic 参数")
            gen_kwargs["topic"] = topic
        elif mode == "script":
            script_path = params.get("script_path")
            if not script_path:
                raise ValueError("mode=script 时必须提供 script_path 参数")
            gen_kwargs["script_path"] = resolve_path(script_path)
        elif mode == "text":
            text = params.get("text")
            if not text:
                raise ValueError("mode=text 时必须提供 text 参数")
            gen_kwargs["text"] = text
        else:
            raise ValueError(f"不支持的 mode: {mode}")

        # 可选参数
        if params.get("title"):
            gen_kwargs["title"] = params["title"]
        if params.get("style"):
            gen_kwargs["style"] = params["style"]
        if params.get("template"):
            gen_kwargs["template_path"] = resolve_path(params["template"])
        if params.get("subtitle_style") is not None:
            gen_kwargs["subtitle_style"] = params["subtitle_style"]
        if params.get("tts_voice"):
            gen_kwargs["voice"] = params["tts_voice"]
        if params.get("count"):
            gen_kwargs["count"] = params["count"]

        # 批量序号，确保每条视频文案不同
        if params.get("batch_index"):
            gen_kwargs["batch_index"] = params["batch_index"]
        if params.get("batch_total"):
            gen_kwargs["batch_total"] = params["batch_total"]

        # 封面相关参数（cover/cover_text/cover_subtitle 通过 gen_kwargs 传递，
        # cover_duration 和 subtitle_animation 通过 config 传递，见 _get_task_config）
        if params.get("cover"):
            gen_kwargs["cover"] = True
            gen_kwargs["cover_text"] = params.get("cover_text") or ""
            gen_kwargs["cover_subtitle"] = params.get("cover_subtitle") or ""

        # ── Step 1: 生成文案 ─────────────────────────
        task.step = "生成文案"
        task.progress = 5

        # 使用进度回调在 generate_single_video 的各步骤间推送进度
        async def on_progress(step: str, percent: int):
            task.step = step
            task.progress = percent
            await ws_manager.broadcast({
                "type": "progress",
                "task_id": task.id,
                "step": step,
                "percent": percent,
            })

        gen_kwargs["progress_callback"] = on_progress

        result = await video_maker.generate_single_video(**gen_kwargs)

        # ── 完成 ────────────────────────────────────
        # 检查任务是否已被取消
        if task.status == "cancelled":
            logger.info(f"任务已取消，跳过完成处理: {task.id}")
            return

        task.status = "completed"
        task.progress = 100
        task.step = "完成"
        task.output_file = result.get("video_path", "")
        task.completed_at = datetime.now().isoformat()

        # 推算发布指南文件路径
        video_path = Path(task.output_file)
        guide_file = str(video_path.parent / f"{video_path.stem}_发布指南.txt") if task.output_file else ""

        await ws_manager.broadcast({
            "type": "complete",
            "task_id": task.id,
            "title": result.get("title", "视频"),
            "output_file": task.output_file,
            "guide_file": guide_file,
            "duration": result.get("duration", 0),
            "elapsed": result.get("elapsed", 0),
        })

        logger.info(f"任务完成: {task.id} -> {task.output_file}")

    except Exception as e:
        # 已取消的任务不需要再标记为 failed
        if task.status == "cancelled":
            logger.info(f"任务已取消，跳过错误处理: {task.id}")
            return

        task.status = "failed"
        task.error = str(e)
        task.completed_at = datetime.now().isoformat()

        await ws_manager.broadcast({
            "type": "error",
            "task_id": task.id,
            "error": str(e),
        })

        logger.error(f"任务失败: {task.id} -> {e}")


async def run_generate_task(task: TaskInfo, config: dict):
    """在并发控制下执行视频生成任务"""
    async with _task_semaphore:
        await _run_generate_task_inner(task, config)


async def run_batch_tasks(batch_id: str, tasks: List[TaskInfo], config: dict):
    """批量执行任务（受全局并发限制）"""

    total = len(tasks)

    async def _run_one(t: TaskInfo, idx: int):
        logger.info(f"批量任务 [{idx+1}/{total}] 开始: {t.params.get('title', t.params.get('topic', '未知'))}")
        # 每个任务使用独立的 config 拷贝，防止并发修改竞态
        import copy
        task_config = copy.deepcopy(config)
        await run_generate_task(t, task_config)

    await asyncio.gather(*[_run_one(t, i) for i, t in enumerate(tasks)])


# ══════════════════════════════════════════════════════
# REST API 端点
# ══════════════════════════════════════════════════════

@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "version": "1.0.0"}


@app.post("/api/generate")
async def generate_video(req: GenerateRequest):
    """提交视频生成任务（支持指定数量，生成多条不同文案的视频）"""
    # 参数校验
    if req.mode == "topic" and not req.topic:
        raise HTTPException(status_code=400, detail="mode=topic 时必须提供 topic 参数")
    if req.mode == "script" and not req.script_path:
        raise HTTPException(status_code=400, detail="mode=script 时必须提供 script_path 参数")
    if req.mode == "text" and not req.text:
        raise HTTPException(status_code=400, detail="mode=text 时必须提供 text 参数")

    # 构建任务参数
    base_params = {
        "mode": req.mode,
        "topic": req.topic,
        "script_path": req.script_path,
        "text": req.text,
        "style": req.style,
        "template": req.template,
        "tts_engine": req.tts_engine,
        "tts_voice": req.tts_voice,
        "subtitle_style": req.subtitle_style,
        "subtitle_animation": req.subtitle_animation,
        "subtitle_position": req.subtitle_position,
        "subtitle_margin": req.subtitle_margin,
        "images": req.images,
        "output_dir": req.output_dir,
        "title": req.title,
        "count": req.count,  # 文案句数
        "cover": req.cover,
        "cover_text": req.cover_text,
        "cover_subtitle": req.cover_subtitle,
        "cover_duration": req.cover_duration,
    }

    # 合并运行时配置
    config = _get_task_config(req.tts_engine, req.tts_voice, req.subtitle_style,
                              req.subtitle_animation, req.cover_duration,
                              req.subtitle_position, req.subtitle_margin)

    if req.video_count == 1:
        # 单条生成
        task = task_manager.create_task(params=base_params)
        asyncio.create_task(run_generate_task(task, config))
        return {"task_id": task.id, "status": "pending", "count": 1}
    else:
        # 多条生成：创建批量任务
        batch_id = uuid.uuid4().hex[:12]
        created_tasks = []

        for i in range(req.video_count):
            params = dict(base_params)
            # 多条视频时，标题自动编号（无论用户是否填了标题）
            # 这确保每条视频输出文件名不冲突
            if req.title:
                params["title"] = f"{req.title}_{i+1:02d}"
            elif req.topic:
                params["title"] = f"{req.topic}_{i+1:02d}"
            else:
                params["title"] = f"视频_{i+1:02d}"
            # 传递批量序号，确保每条视频文案不同
            params["batch_index"] = i + 1
            params["batch_total"] = req.video_count
            # 自定义文案模式下多条视频文案相同，提示用户
            if req.mode == "text" and req.video_count > 1:
                params["_batch_note"] = "自定义文案模式下多条视频将使用相同文案"

            task = task_manager.create_task(params=params, batch_id=batch_id)
            created_tasks.append(task)

        # 异步启动批量任务
        asyncio.create_task(run_batch_tasks(batch_id, created_tasks, config))

        return {
            "batch_id": batch_id,
            "task_ids": [t.id for t in created_tasks],
            "status": "pending",
            "count": len(created_tasks),
        }

@app.post("/api/schedules")
async def create_schedule(req: ScheduleRequest):
    """创建定时/周期任务"""
    if not schedule_manager.has_apscheduler:
        raise HTTPException(status_code=500, detail="未安装 apscheduler，定时任务功能不可用")
    try:
        job_id = schedule_manager.add_schedule(req)
        return {"job_id": job_id, "status": "scheduled"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/schedules")
async def get_schedules():
    """获取所有定时/周期任务"""
    return {"schedules": schedule_manager.get_all()}

@app.delete("/api/schedules/{job_id}")
async def delete_schedule(job_id: str):
    """删除定时/周期任务"""
    schedule_manager.delete_schedule(job_id)
    return {"message": f"任务 {job_id} 已删除"}




@app.post("/api/batch")
async def batch_generate(req: BatchRequest):
    """批量提交视频生成任务"""
    batch_id = uuid.uuid4().hex[:12]
    tasks = []

    for item in req.tasks:
        params = {
            "mode": "topic" if item.topic else ("script" if item.script_path else "text"),
            "topic": item.topic,
            "script_path": item.script_path,
            "text": item.text,
            "style": item.style,
            "template": item.template,
            "tts_engine": item.tts_engine,
            "tts_voice": item.tts_voice,
            "subtitle_style": item.subtitle_style,
            "subtitle_animation": item.subtitle_animation,
            "subtitle_position": item.subtitle_position,
            "subtitle_margin": item.subtitle_margin,
            "images": req.images,
            "title": item.title,
            "cover": item.cover,
            "cover_text": item.cover_text,
            "cover_subtitle": item.cover_subtitle,
            "cover_duration": item.cover_duration,
        }
        task = task_manager.create_task(params=params, batch_id=batch_id)
        tasks.append(task)

    # 异步启动批量任务（深拷贝配置防止并发冲突）
    import copy
    config = copy.deepcopy(runtime_config)
    asyncio.create_task(run_batch_tasks(batch_id, tasks, config))

    return {
        "batch_id": batch_id,
        "task_ids": [t.id for t in tasks],
        "status": "pending",
        "count": len(tasks),
    }


@app.get("/api/tasks")
async def list_tasks(
    status: Optional[str] = None,
    batch_id: Optional[str] = None,
):
    """获取任务列表"""
    tasks = task_manager.list_tasks(status=status, batch_id=batch_id)
    return {"tasks": [t.to_dict() for t in tasks], "total": len(tasks)}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """获取单个任务状态"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return task.to_dict()

@app.post("/api/tasks/{task_id}/publish")
async def publish_task(task_id: str):
    """一键发布单个任务到抖音"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="只能发布已完成的视频")
    if not task.output_file or not Path(task.output_file).exists():
        raise HTTPException(status_code=404, detail="视频文件不存在")

    # 推算指南文件
    video_path = Path(task.output_file)
    guide_file = str(video_path.parent / f"{video_path.stem}_发布指南.txt")
    
    # 执行异步发布
    success, msg = await publish_utils.publish_video(str(video_path), guide_file)
    if success:
        return {"status": "success", "message": msg}
    else:
        raise HTTPException(status_code=500, detail=msg)

@app.post("/api/publish/schedule")
async def schedule_publish_tasks(req: SchedulePublishRequest):
    """批量加入抖音发布队列"""
    from datetime import datetime, timedelta
    
    start = datetime.now()
    if req.start_time:
        if ":" in req.start_time and "T" not in req.start_time:
            # HH:MM format
            try:
                h, m = map(int, req.start_time.split(":"))
                start = start.replace(hour=h, minute=m, second=0, microsecond=0)
                if start < datetime.now():
                    start += timedelta(days=1)
            except:
                pass
        else:
            try:
                start = datetime.fromisoformat(req.start_time)
            except:
                pass
                
    added = 0
    for i, tid in enumerate(req.task_ids):
        task = task_manager.get_task(tid)
        if not task or task.status != "completed" or not task.output_file:
            continue
            
        video_path = Path(task.output_file)
        guide_file = str(video_path.parent / f"{video_path.stem}_发布指南.txt")
        run_at = start + timedelta(hours=i * req.interval_hours)
        
        publish_utils.add_to_publish_schedule(tid, str(video_path), guide_file, task.params.get("title", ""), run_at)
        added += 1
        
    return {"status": "success", "added": added, "message": f"已将 {added} 个视频加入发布队列"}

@app.get("/api/publish/queue")
async def get_publish_queue():
    """获取发布队列"""
    return {"queue": publish_utils.get_publish_jobs()}

@app.delete("/api/publish/queue/{job_id}")
async def delete_publish_job(job_id: str):
    """从发布队列移除"""
    success = publish_utils.delete_publish_job(job_id)
    return {"status": "success" if success else "failed"}


@app.get("/api/tasks/{task_id}/download")
async def download_video(task_id: str):
    """下载生成的视频"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    if task.status != "completed":
        raise HTTPException(status_code=400, detail=f"任务状态为 {task.status}，尚未完成")
    if not task.output_file or not Path(task.output_file).exists():
        raise HTTPException(status_code=404, detail="视频文件不存在")

    filename = Path(task.output_file).name
    return FileResponse(
        path=task.output_file,
        media_type="video/mp4",
        filename=filename,
        content_disposition_type="attachment",
    )


@app.get("/api/tasks/{task_id}/preview")
async def preview_video(task_id: str):
    """在线预览生成的视频（inline 模式，支持浏览器直接播放）"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    if task.status != "completed":
        raise HTTPException(status_code=400, detail=f"任务状态为 {task.status}，尚未完成")
    if not task.output_file or not Path(task.output_file).exists():
        raise HTTPException(status_code=404, detail="视频文件不存在")

    file_path = Path(task.output_file)
    file_size = file_path.stat().st_size

    def iter_file():
        with open(file_path, "rb") as f:
            while chunk := f.read(64 * 1024):
                yield chunk

    from starlette.responses import StreamingResponse
    from urllib.parse import quote
    # RFC 5987: 使用 filename* 编码非 ASCII 文件名，避免 latin-1 编码崩溃
    safe_filename = quote(file_path.name)
    return StreamingResponse(
        iter_file(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{safe_filename}",
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
        },
    )


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    """取消/删除任务"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    if task.status == "running":
        # 运行中的任务只标记取消，不删除（等异步任务自然结束后再清理）
        task.status = "cancelled"
        task.error = "用户手动取消"
        task.completed_at = datetime.now().isoformat()
        await ws_manager.broadcast({
            "type": "error",
            "task_id": task.id,
            "error": "用户手动取消",
        })
        return {"message": f"任务 {task_id} 已取消", "status": "cancelled"}
    # 非运行中的任务可直接删除
    task_manager.delete_task(task_id)
    return {"message": f"任务 {task_id} 已删除"}


class OpenFolderRequest(BaseModel):
    path: str


@app.post("/api/open-folder")
async def open_folder(req: OpenFolderRequest):
    """打开本地文件夹（仅在本地运行时有效）"""
    import subprocess
    import platform

    folder = req.path
    if not Path(folder).exists():
        raise HTTPException(status_code=404, detail=f"目录不存在: {folder}")

    # 安全检查：只允许打开项目输出目录
    allowed_dirs = [
        resolve_path("输出"),
        resolve_path("素材"),
        resolve_path("音乐"),
        str(Path.home() / "Documents"),
        str(Path.home() / "Desktop"),
        str(Path.home() / "Downloads"),
    ]
    folder_abs = str(Path(folder).resolve())
    allowed = any(folder_abs.startswith(str(Path(d).resolve())) for d in allowed_dirs if Path(d).exists())

    # 也允许输出目录的子目录
    output_dir_abs = str(Path(resolve_path(config.get("batch", {}).get("output_dir", "输出"))).resolve())
    if folder_abs.startswith(output_dir_abs):
        allowed = True

    if not allowed:
        raise HTTPException(status_code=403, detail="不允许打开此目录")

    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", folder])
        elif system == "Windows":
            subprocess.Popen(["explorer", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打开文件夹失败: {e}")


@app.get("/api/config")
async def get_config():
    """获取当前配置"""
    return runtime_config


@app.put("/api/config")
async def update_config(req: ConfigUpdateRequest):
    """更新运行时配置（不写回文件）"""
    global runtime_config

    if req.api:
        runtime_config["api"] = video_maker._deep_merge(runtime_config["api"], req.api)
    if req.video:
        runtime_config["video"] = video_maker._deep_merge(runtime_config["video"], req.video)
    if req.audio:
        runtime_config["audio"] = video_maker._deep_merge(runtime_config["audio"], req.audio)
    if req.batch:
        runtime_config["batch"] = video_maker._deep_merge(runtime_config["batch"], req.batch)
    if req.tts:
        runtime_config["tts"] = video_maker._deep_merge(runtime_config["tts"], req.tts)

    return {"message": "配置已更新", "config": runtime_config}


@app.get("/api/templates")
async def list_templates():
    """获取商家模板列表"""
    templates_dir = resolve_path("templates")
    result = []

    if Path(templates_dir).exists():
        for p in sorted(Path(templates_dir).glob("*.yaml")):
            template_data = video_maker.load_template(str(p))
            merchant = template_data.get("merchant", {})
            result.append({
                "path": f"templates/{p.name}",
                "name": merchant.get("name", p.stem),
                "full_name": merchant.get("full_name", ""),
                "filename": p.name,
            })

        # 同时扫描 yml 文件
        for p in sorted(Path(templates_dir).glob("*.yml")):
            template_data = video_maker.load_template(str(p))
            merchant = template_data.get("merchant", {})
            result.append({
                "path": f"templates/{p.name}",
                "name": merchant.get("name", p.stem),
                "full_name": merchant.get("full_name", ""),
                "filename": p.name,
            })

    return {"templates": result}


@app.get("/api/styles")
async def list_styles():
    """获取文案风格 + 字幕样式列表"""
    # 文案风格（硬编码常见风格，video_maker.py 通过 style 参数传给 AI）
    script_styles = [
        {"id": "团购种草", "name": "团购种草", "description": "适合团购推广，突出优惠和卖点"},
        {"id": "探店推荐", "name": "探店推荐", "description": "探店风格，真实体验口吻"},
        {"id": "种草攻略", "name": "种草攻略", "description": "攻略风格，干货满满"},
        {"id": "好物分享", "name": "好物分享", "description": "分享风格，亲切自然"},
        {"id": "痛点切入", "name": "痛点切入", "description": "从痛点出发，引发共鸣"},
        {"id": "对比测评", "name": "对比测评", "description": "对比风格，理性分析"},
    ]

    # 字幕样式（来自 video_maker.SUBTITLE_STYLES）
    subtitle_styles = []
    for sid, style in video_maker.SUBTITLE_STYLES.items():
        subtitle_styles.append({
            "id": sid,
            "name": style["name"],
            "font_size": style["font_size"],
            "position": style["position"],
            "margin": style.get("margin", 100),
            "color": list(style.get("color", (255,255,255))),
            "bg": list(style.get("bg", (0,0,0,180))),
            "highlight_words": style.get("highlight_words", []),
            "highlight_color": list(style.get("highlight_color", (255,230,0))),
        })

    return {
        "script_styles": script_styles,
        "subtitle_styles": subtitle_styles,
    }


@app.get("/api/tts-voices")
async def list_tts_voices():
    """获取 TTS 音色列表（动态从 edge_tts 获取可用语音）"""
    edge_voices = []

    try:
        import edge_tts
        # 动态获取 Edge TTS 可用语音，避免硬编码过时
        raw_voices = await edge_tts.list_voices()
        zh_voices = [v for v in raw_voices if v["Locale"].startswith("zh-CN")]

        # 语音名称友好映射
        _NAME_MAP = {
            "zh-CN-XiaoxiaoNeural": "晓晓（女声·温柔）",
            "zh-CN-XiaoyiNeural": "晓伊（女声·活泼）",
            "zh-CN-YunjianNeural": "云健（男声·沉稳）",
            "zh-CN-YunxiNeural": "云希（男声·阳光）",
            "zh-CN-YunxiaNeural": "云夏（男声·少年）",
            "zh-CN-YunyangNeural": "云扬（男声·新闻）",
            "zh-CN-liaoning-XiaobeiNeural": "晓北（东北·女声）",
            "zh-CN-shaanxi-XiaoniNeural": "晓妮（陕西·女声）",
        }

        for v in zh_voices:
            short_name = v["ShortName"]
            friendly_name = _NAME_MAP.get(short_name, v.get("FriendlyName", short_name))
            gender = "女声" if v["Gender"] == "Female" else "男声"
            edge_voices.append({
                "id": short_name,
                "name": friendly_name,
                "engine": "edge_tts",
                "gender": gender,
            })
    except Exception as e:
        logger.warning(f"无法获取 Edge TTS 语音列表: {e}")
        # 回退到硬编码的最小列表（仅包含已验证可用的语音）
        edge_voices = [
            {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓（女声·温柔）", "engine": "edge_tts"},
            {"id": "zh-CN-YunxiNeural", "name": "云希（男声·阳光）", "engine": "edge_tts"},
            {"id": "zh-CN-YunjianNeural", "name": "云健（男声·沉稳）", "engine": "edge_tts"},
        ]

    # MiMo TTS 音色
    mimo_voices = [
        {"id": "mimo_default", "name": "MiMo 默认", "engine": "mimo_tts"},
    ]

    return {
        "engines": [
            {"id": "edge_tts", "name": "Edge TTS（免费）", "voices": edge_voices},
            {"id": "mimo_tts", "name": "MiMo TTS（高质量）", "voices": mimo_voices},
        ],
        "default_engine": runtime_config["audio"].get("tts_engine", "edge_tts"),
        "default_voice": runtime_config["audio"].get("edge_tts_voice", "zh-CN-XiaoxiaoNeural"),
    }


@app.get("/api/images")
async def list_images(dir: Optional[str] = None):
    """扫描素材目录，返回可用图片列表"""
    search_dirs = []
    if dir:
        search_dirs.append(resolve_path(dir))
    else:
        # 默认扫描素材目录和素材库
        search_dirs.append(resolve_path("素材"))
        search_dirs.append(resolve_path("素材库"))

    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".mp4", ".mov", ".avi", ".webm"}
    images = []

    for search_dir in search_dirs:
        base = Path(search_dir)
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.suffix.lower() in exts and p.is_file():
                rel_path = str(p.relative_to(Path(BASE_DIR)))
                stat = p.stat()
                images.append({
                    "path": rel_path,
                    "filename": p.name,
                    "size": stat.st_size,
                    "size_mb": round(stat.st_size / 1024 / 1024, 2),
                })

    return {"images": images, "total": len(images)}


@app.get("/api/scripts")
async def list_scripts(dir: Optional[str] = None):
    """扫描脚本目录，返回可用脚本列表"""
    search_dirs = []
    if dir:
        search_dirs.append(resolve_path(dir))
    else:
        # 默认扫描脚本目录
        search_dirs.append(resolve_path("脚本"))

    exts = {".txt", ".md"}
    scripts = []

    for search_dir in search_dirs:
        base = Path(search_dir)
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.suffix.lower() in exts and p.is_file():
                rel_path = str(p.relative_to(Path(BASE_DIR)))
                # 读取前几行作为预览
                try:
                    preview = p.read_text(encoding="utf-8")[:200]
                except Exception:
                    preview = ""
                scripts.append({
                    "path": rel_path,
                    "filename": p.name,
                    "preview": preview,
                })

    return {"scripts": scripts, "total": len(scripts)}


@app.post("/api/preview-script")
async def preview_script(req: dict):
    """预览 AI 生成的文案（不生成视频）"""
    topic = req.get("topic")
    style = req.get("style", "团购种草")
    count = req.get("count", 8)
    template_path = req.get("template")

    if not topic:
        raise HTTPException(status_code=400, detail="必须提供 topic 参数")

    config = runtime_config.copy()
    template = None
    if template_path:
        try:
            template = video_maker.load_template(resolve_path(template_path))
        except Exception:
            pass

    try:
        sentences = await video_maker.generate_script(
            topic=topic,
            config=config,
            style=style,
            count=count,
            template=template,
        )
        return {"sentences": sentences, "count": len(sentences)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文案生成失败: {str(e)}")


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), dir: Optional[str] = None):
    """上传素材图片，保存到素材目录"""
    target_dir = resolve_path(dir) if dir else resolve_path("素材")
    Path(target_dir).mkdir(parents=True, exist_ok=True)

    allowed_exts = {".jpg", ".jpeg", ".png", ".webp"}
    uploaded = []
    errors = []

    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in allowed_exts:
            errors.append({
                "filename": file.filename,
                "error": f"不支持的文件格式: {ext}，仅支持 jpg/png/webp",
            })
            continue

        dest = Path(target_dir) / file.filename
        # 避免文件名冲突
        if dest.exists():
            stem = dest.stem
            counter = 1
            while dest.exists():
                dest = Path(target_dir) / f"{stem}_{counter}{ext}"
                counter += 1

        try:
            content = await file.read()
            dest.write_bytes(content)
            rel_path = str(dest.relative_to(Path(BASE_DIR)))
            uploaded.append({
                "filename": dest.name,
                "path": rel_path,
                "size": len(content),
            })
        except Exception as e:
            errors.append({
                "filename": file.filename,
                "error": str(e),
            })

    return {
        "uploaded": uploaded,
        "errors": errors,
        "total_uploaded": len(uploaded),
        "total_errors": len(errors),
    }


# ══════════════════════════════════════════════════════
# 素材库管理系统
# ══════════════════════════════════════════════════════

ASSET_LIB_DIR = Path(BASE_DIR) / "素材库"
ASSET_INDEX_FILE = ASSET_LIB_DIR / "index.json"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
DOC_EXTS = {".txt", ".md", ".doc", ".docx", ".pdf"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


def _load_asset_index() -> dict:
    """加载素材库索引"""
    if ASSET_INDEX_FILE.exists():
        try:
            return json.loads(ASSET_INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 返回默认结构
    return {"version": "1.0", "categories": {}, "assets": {}, "documents": {}}


def _save_asset_index(index: dict):
    """保存素材库索引"""
    ASSET_LIB_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _file_type_category(suffix: str) -> str:
    """根据文件后缀判断素材类型"""
    s = suffix.lower()
    if s in IMAGE_EXTS:
        return "图片"
    elif s in VIDEO_EXTS:
        return "视频"
    elif s in AUDIO_EXTS:
        return "音乐"
    elif s in DOC_EXTS:
        return "文案资料"
    return "其他"


def _scan_asset_dir() -> dict:
    """扫描素材库目录，返回结构化的素材列表"""
    result = {"images": [], "videos": [], "documents": [], "audio": [], "total": 0}

    if not ASSET_LIB_DIR.exists():
        return result

    # 预加载索引（避免每个文件都读取一次）
    index = _load_asset_index()

    for p in sorted(ASSET_LIB_DIR.rglob("*")):
        if not p.is_file():
            # 也处理 symlink（is_file 对 broken symlink 返回 False，但 symlink 本身有效）
            if p.is_symlink():
                target = p.resolve()
                if not target.exists():
                    continue  # broken symlink
            else:
                continue
        if p.name == "index.json":
            continue

        suffix = p.suffix.lower()
        rel_path = str(p.relative_to(ASSET_LIB_DIR))
        try:
            stat = p.stat()
        except OSError:
            continue
        category = _file_type_category(suffix)

        # 判断来源
        asset_meta = index.get("assets", {}).get(rel_path, {})
        source = asset_meta.get("source", "local")
        original_path = asset_meta.get("original_path", "")

        item = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, rel_path))[:12],
            "path": f"素材库/{rel_path}",
            "rel_path": rel_path,
            "filename": p.name,
            "size": stat.st_size,
            "size_mb": round(stat.st_size / 1024 / 1024, 2),
            "category": category,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "source": source,
            "original_path": original_path,
            "tags": asset_meta.get("tags", []),
            "ai_description": asset_meta.get("ai_description", ""),
        }

        if category == "图片":
            result["images"].append(item)
        elif category == "视频":
            result["videos"].append(item)
        elif category == "音乐":
            result["audio"].append(item)
        elif category in ("文案资料",):
            # 读取内容预览
            try:
                preview = p.read_text(encoding="utf-8")[:500]
            except Exception:
                preview = ""
            item["preview"] = preview
            result["documents"].append(item)

        result["total"] += 1

    return result


@app.get("/api/assets")
async def list_assets(category: Optional[str] = None, tag: Optional[str] = None, search: Optional[str] = None):
    """获取素材库列表（支持筛选和搜索）"""
    data = _scan_asset_dir()

    all_items = []
    all_items.extend(data["images"])
    all_items.extend(data["videos"])
    all_items.extend(data["audio"])
    all_items.extend(data["documents"])

    # 筛选
    if category:
        all_items = [i for i in all_items if i["category"] == category]
    if tag:
        all_items = [i for i in all_items if tag in i.get("tags", [])]
    if search:
        search_lower = search.lower()
        all_items = [i for i in all_items
                     if search_lower in i["filename"].lower()
                     or search_lower in i.get("ai_description", "").lower()
                     or any(search_lower in t.lower() for t in i.get("tags", []))]

    # 分类统计
    stats = {}
    for item in data["images"] + data["videos"] + data["audio"] + data["documents"]:
        cat = item["category"]
        stats[cat] = stats.get(cat, 0) + 1

    return {
        "items": all_items,
        "stats": stats,
        "total": len(all_items),
        "categories": _load_asset_index().get("categories", {}),
    }


@app.post("/api/assets/upload")
async def upload_asset(
    files: List[UploadFile] = File(...),
    category: str = "图片",
    subcategory: str = "其他",
    tags: Optional[str] = None,
):
    """上传素材到素材库（支持指定分类和标签）"""
    target_dir = ASSET_LIB_DIR / category / subcategory
    target_dir.mkdir(parents=True, exist_ok=True)

    all_exts = IMAGE_EXTS | VIDEO_EXTS | DOC_EXTS | AUDIO_EXTS
    uploaded = []
    errors = []

    tag_list = [t.strip() for t in tags.split(",")] if tags else []

    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in all_exts:
            errors.append({"filename": file.filename, "error": f"不支持的格式: {ext}"})
            continue

        dest = target_dir / file.filename
        if dest.exists():
            stem = dest.stem
            counter = 1
            while dest.exists():
                dest = target_dir / f"{stem}_{counter}{ext}"
                counter += 1

        try:
            content = await file.read()
            dest.write_bytes(content)
            rel_path = str(dest.relative_to(ASSET_LIB_DIR))

            # 更新索引
            index = _load_asset_index()
            index.setdefault("assets", {})[rel_path] = {
                "tags": tag_list,
                "ai_description": "",
                "source": "upload",
                "uploaded_at": datetime.now().isoformat(),
            }
            _save_asset_index(index)

            uploaded.append({
                "filename": dest.name,
                "path": f"素材库/{rel_path}",
                "size": len(content),
                "tags": tag_list,
            })
        except Exception as e:
            errors.append({"filename": file.filename, "error": str(e)})

    return {"uploaded": uploaded, "errors": errors, "total_uploaded": len(uploaded)}


class CloudAssetRequest(BaseModel):
    """云端素材导入请求"""
    url: str = Field(..., description="图片/视频 URL")
    category: str = Field("图片", description="分类: 图片/视频")
    subcategory: str = Field("其他", description="子分类")
    tags: Optional[str] = Field(None, description="标签（逗号分隔）")
    filename: Optional[str] = Field(None, description="保存文件名（不含后缀）")


@app.post("/api/assets/import-cloud")
async def import_cloud_asset(req: CloudAssetRequest):
    """从 URL 导入云端素材（图片/视频）"""
    import httpx

    tag_list = [t.strip() for t in req.tags.split(",")] if req.tags else []

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(req.url)
            resp.raise_for_status()
            content = resp.content

        # 从 URL 或 Content-Type 推断文件后缀
        content_type = resp.headers.get("content-type", "")
        ext_map = {
            "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
            "video/mp4": ".mp4", "video/quicktime": ".mov",
        }
        ext = ext_map.get(content_type.split(";")[0], Path(req.url).suffix or ".jpg")

        # 文件名
        if req.filename:
            fname = req.filename + ext
        else:
            fname = Path(req.url).stem or f"cloud_{uuid.uuid4().hex[:6]}"
            fname = fname + ext

        # 保存
        target_dir = ASSET_LIB_DIR / req.category / req.subcategory
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / fname
        dest.write_bytes(content)

        rel_path = str(dest.relative_to(ASSET_LIB_DIR))

        # 更新索引
        index = _load_asset_index()
        index.setdefault("assets", {})[rel_path] = {
            "tags": tag_list,
            "ai_description": "",
            "source": "cloud",
            "source_url": req.url,
            "imported_at": datetime.now().isoformat(),
        }
        _save_asset_index(index)

        return {
            "filename": fname,
            "path": f"素材库/{rel_path}",
            "size": len(content),
            "tags": tag_list,
            "source_url": req.url,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"云端素材导入失败: {str(e)}")


@app.put("/api/assets/{asset_id}/tags")
async def update_asset_tags(asset_id: str, req: dict):
    """更新素材标签"""
    tags = req.get("tags", [])
    index = _load_asset_index()

    # 查找对应素材
    found = None
    for rel_path, meta in index.get("assets", {}).items():
        if str(uuid.uuid5(uuid.NAMESPACE_URL, rel_path))[:12] == asset_id:
            found = rel_path
            break

    if not found:
        raise HTTPException(status_code=404, detail=f"素材 {asset_id} 不存在")

    index["assets"][found]["tags"] = tags
    _save_asset_index(index)
    return {"message": "标签已更新", "tags": tags}


class AnalyzeDocumentRequest(BaseModel):
    """文案资料 AI 分析请求"""
    path: str = Field(..., description="文案资料文件路径")
    extract_type: str = Field("all", description="提取类型: all/links/key_points/contacts/pricing")


@app.post("/api/assets/analyze")
async def analyze_document(req: AnalyzeDocumentRequest):
    """AI 分析文案资料 — 提取有用信息、链接、关键点"""
    file_path = resolve_path(req.path)

    if not Path(file_path).exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {req.path}")

    # 读取文件内容
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件读取失败: {str(e)}")

    if not content.strip():
        raise HTTPException(status_code=400, detail="文件内容为空")

    # 构建分析 Prompt
    extract_type = req.extract_type
    prompts = {
        "all": """请分析以下文案资料，提取以下信息并以 JSON 格式返回：
1. "key_points": 关键要点列表（每条不超过20字）
2. "links": 所有 URL 链接列表（含链接说明）
3. "contacts": 联系方式列表（电话、地址、微信等）
4. "pricing": 价格信息列表（服务/价格/优惠）
5. "services": 服务项目列表
6. "summary": 50字以内的内容摘要
7. "suggested_tags": 建议标签列表（3-5个）
8. "suggested_topics": 可用于生成视频的主题列表（3-5个）

文案资料内容：
---
{content}""",
        "links": """从以下文案资料中提取所有 URL 链接，以 JSON 格式返回：
{{"links": [{{"url": "链接地址", "description": "链接说明"}}]}}

文案资料内容：
---
{content}""",
        "key_points": """从以下文案资料中提取关键要点，以 JSON 格式返回：
{{"key_points": ["要点1", "要点2", ...]}}

文案资料内容：
---
{content}""",
        "contacts": """从以下文案资料中提取联系方式，以 JSON 格式返回：
{{"contacts": [{{"type": "电话/地址/微信等", "value": "具体值"}}]}}

文案资料内容：
---
{content}""",
        "pricing": """从以下文案资料中提取价格信息，以 JSON 格式返回：
{{"pricing": [{{"service": "服务名", "price": "价格", "note": "优惠说明"}}]}}

文案资料内容：
---
{content}""",
    }

    prompt = prompts.get(extract_type, prompts["all"]).format(content=content[:3000])

    # 调用 LLM
    config = runtime_config.copy()
    try:
        import httpx

        api_key = os.environ.get("XIAOMI_API_KEY", config["api"].get("xiaomi_api_key", ""))
        base_url = config["api"].get("xiaomi_base_url", "https://token-plan-cn.xiaomimimo.com/v1")
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": config["api"].get("llm_model", "mimo-v2.5-pro"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 2000,
        }

        async with httpx.AsyncClient(timeout=config["api"].get("timeout", 120)) as client:
            result = await video_maker.api_call_with_retry(
                client=client,
                url=url,
                headers=headers,
                payload=payload,
                max_retries=config["api"].get("max_retries", 3),
                retry_delay=config["api"].get("retry_delay", 2.0),
                timeout=config["api"].get("timeout", 120),
            )

        # api_call_with_retry 返回的是原始响应 JSON
        ai_content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        # 尝试提取 JSON
        import re
        json_match = re.search(r'\{[\s\S]*\}', ai_content)
        if json_match:
            try:
                analysis = json.loads(json_match.group())
            except json.JSONDecodeError:
                analysis = {"raw": ai_content}
        else:
            analysis = {"raw": ai_content}

        # 更新素材索引
        index = _load_asset_index()
        rel_path = str(Path(file_path).relative_to(Path(BASE_DIR)))
        if rel_path in index.get("assets", {}):
            index["assets"][rel_path]["ai_description"] = ai_content[:200]
        else:
            index.setdefault("assets", {})[rel_path] = {
                "tags": analysis.get("suggested_tags", []),
                "ai_description": ai_content[:200],
                "source": "upload",
                "analyzed_at": datetime.now().isoformat(),
            }
        _save_asset_index(index)

        return {
            "path": req.path,
            "extract_type": extract_type,
            "analysis": analysis,
            "raw_ai_response": ai_content,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 分析失败: {str(e)}")


class UploadDocumentRequest(BaseModel):
    """文案资料上传请求"""
    content: str = Field(..., description="文案内容（文本）")
    filename: Optional[str] = Field(None, description="文件名")
    tags: Optional[str] = Field(None, description="标签（逗号分隔）")
    auto_analyze: bool = Field(True, description="是否自动 AI 分析")


@app.post("/api/assets/document")
async def upload_document(req: UploadDocumentRequest):
    """上传文案资料（文本内容），支持自动 AI 分析"""
    tag_list = [t.strip() for t in req.tags.split(",")] if req.tags else []

    # 生成文件名
    filename = req.filename or f"资料_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if not filename.endswith(".txt"):
        filename += ".txt"

    # 保存
    target_dir = ASSET_LIB_DIR / "文案资料"
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / filename
    if dest.exists():
        dest = target_dir / f"{Path(filename).stem}_{uuid.uuid4().hex[:4]}.txt"

    dest.write_text(req.content, encoding="utf-8")
    rel_path = str(dest.relative_to(ASSET_LIB_DIR))

    # 更新索引
    index = _load_asset_index()
    index.setdefault("assets", {})[rel_path] = {
        "tags": tag_list,
        "ai_description": "",
        "source": "upload",
        "uploaded_at": datetime.now().isoformat(),
    }
    _save_asset_index(index)

    result = {
        "filename": dest.name,
        "path": f"素材库/{rel_path}",
        "size": len(req.content.encode("utf-8")),
        "tags": tag_list,
        "analysis": None,
    }

    # 自动分析
    if req.auto_analyze:
        try:
            analysis_resp = await analyze_document(AnalyzeDocumentRequest(
                path=f"素材库/{rel_path}",
                extract_type="all",
            ))
            result["analysis"] = analysis_resp.get("analysis")
        except Exception as e:
            result["analysis_error"] = str(e)

    return result


@app.get("/api/assets/categories")
async def list_asset_categories():
    """获取素材库分类结构"""
    index = _load_asset_index()
    return {"categories": index.get("categories", {})}


class LinkFolderRequest(BaseModel):
    """关联本地文件夹请求"""
    folder_path: str = Field(..., description="本地文件夹绝对路径")
    category: str = Field("图片", description="素材分类: 图片/视频/文案资料/音乐")
    subcategory: str = Field("其他", description="子分类（如 宠物寄养/门店环境 等）")
    recursive: bool = Field(True, description="是否递归扫描子目录")


@app.post("/api/assets/link-folder")
async def link_local_folder(req: LinkFolderRequest):
    """关联本地文件夹 — 扫描文件并创建软链接到素材库，无需手动上传"""
    folder = Path(req.folder_path).expanduser().resolve()

    if not folder.exists():
        raise HTTPException(status_code=400, detail=f"文件夹不存在: {req.folder_path}")
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是文件夹: {req.folder_path}")

    # 安全检查：防止访问系统目录
    dangerous_paths = ["/System", "/usr", "/bin", "/sbin", "/etc", "/var", "/dev", "/proc"]
    for dp in dangerous_paths:
        if str(folder).startswith(dp):
            raise HTTPException(status_code=400, detail=f"不允许访问系统目录: {req.folder_path}")

    # 目标子分类目录
    target_dir = ASSET_LIB_DIR / req.category / req.subcategory
    target_dir.mkdir(parents=True, exist_ok=True)

    # 根据分类确定要扫描的扩展名
    ext_map = {
        "图片": IMAGE_EXTS,
        "视频": VIDEO_EXTS,
        "音乐": AUDIO_EXTS,
        "文案资料": DOC_EXTS,
    }
    valid_exts = ext_map.get(req.category, IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | DOC_EXTS)

    # 扫描文件
    if req.recursive:
        files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in valid_exts]
    else:
        files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in valid_exts]

    if not files:
        return {
            "linked": 0,
            "skipped": 0,
            "errors": 0,
            "message": f"文件夹中没有找到支持的素材文件（{req.category}）",
        }

    # 创建软链接并更新索引
    index = _load_asset_index()
    linked = 0
    skipped = 0
    errors = 0

    for src_file in sorted(files):
        try:
            # 链接文件名：用相对路径结构避免重名，把路径分隔符替换为 _
            rel_from_base = src_file.relative_to(folder)
            link_name = str(rel_from_base).replace("/", "_").replace("\\", "_")
            link_path = target_dir / link_name

            if link_path.exists():
                # 检查是否已经指向同一个文件
                if link_path.is_symlink() and link_path.resolve() == src_file.resolve():
                    skipped += 1
                    continue
                # 已存在同名文件（非 symlink），跳过
                if not link_path.is_symlink():
                    skipped += 1
                    continue
                # 旧的 symlink 指向不同文件，重建
                link_path.unlink()

            link_path.symlink_to(src_file)
            rel_path = str(link_path.relative_to(ASSET_LIB_DIR))

            # 更新索引
            stat = src_file.stat()
            index.setdefault("assets", {})[rel_path] = {
                "tags": [],
                "ai_description": "",
                "source": "link",
                "original_path": str(src_file),
                "linked_at": datetime.now().isoformat(),
                "size": stat.st_size,
            }
            linked += 1

        except Exception as e:
            logger.warning(f"关联文件失败 {src_file}: {e}")
            errors += 1

    _save_asset_index(index)

    return {
        "linked": linked,
        "skipped": skipped,
        "errors": errors,
        "folder": str(folder),
        "category": req.category,
        "subcategory": req.subcategory,
        "message": f"成功关联 {linked} 个文件" + (f"，跳过 {skipped} 个" if skipped else "") + (f"，{errors} 个失败" if errors else ""),
    }


@app.get("/api/local-file/{file_path:path}")
async def serve_local_file(file_path: str):
    """通用本地文件服务 — 用于预览素材库以外的本地文件（如 symlink 目标）"""
    # 安全检查
    full_path = Path(file_path).expanduser().resolve()

    # 敏感目录黑名单
    home = Path.home()
    sensitive_dirs = [
        home / ".ssh",
        home / ".gnupg",
        home / ".aws",
        home / ".config",
        home / ".kube",
        home / ".npmrc",
        home / ".pypirc",
        home / ".netrc",
        home / ".workbuddy",
    ]
    for sd in sensitive_dirs:
        if str(full_path).startswith(str(sd)):
            raise HTTPException(status_code=403, detail="不允许访问敏感目录")

    # 只允许访问用户目录和项目目录下的文件
    allowed_prefixes = [home / "Documents", home / "Pictures", home / "Desktop", home / "Downloads", Path(BASE_DIR)]
    if not any(str(full_path).startswith(str(p)) for p in allowed_prefixes):
        raise HTTPException(status_code=403, detail="不允许访问此路径")

    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(str(full_path))


@app.delete("/api/assets/{asset_id}")
async def delete_asset(asset_id: str):
    """删除素材"""
    index = _load_asset_index()

    found_path = None
    for rel_path, meta in index.get("assets", {}).items():
        if str(uuid.uuid5(uuid.NAMESPACE_URL, rel_path))[:12] == asset_id:
            found_path = rel_path
            break

    if not found_path:
        raise HTTPException(status_code=404, detail=f"素材 {asset_id} 不存在")

    # 删除文件
    file_path = ASSET_LIB_DIR / found_path
    if file_path.exists():
        file_path.unlink()

    # 更新索引
    del index["assets"][found_path]
    _save_asset_index(index)

    return {"message": f"素材已删除: {found_path}"}


# ══════════════════════════════════════════════════════
# WebSocket 端点
# ══════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 实时进度推送"""
    await ws_manager.connect(websocket)
    try:
        while True:
            # 接收客户端消息（心跳/请求）
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue

            # 处理客户端请求
            msg_type = msg.get("type", "")

            if msg_type == "ping":
                await ws_manager.send_to(websocket, {"type": "pong"})

            elif msg_type == "subscribe":
                # 客户端订阅后，推送当前所有任务状态
                tasks = task_manager.list_tasks()
                await ws_manager.send_to(websocket, {
                    "type": "init",
                    "tasks": [t.to_dict() for t in tasks],
                })

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket 异常: {e}")
        ws_manager.disconnect(websocket)


# ══════════════════════════════════════════════════════
# 静态文件服务（前端 build 产物）
# ══════════════════════════════════════════════════════

def _mount_static_files():
    """挂载前端文件到 /"""
    # 优先使用 dist 目录（Vite build 产物），其次使用 web 目录（纯 HTML）
    web_dir = Path(BASE_DIR) / "web"
    dist_dir = web_dir / "dist"
    static_dir = dist_dir if dist_dir.exists() else web_dir

    from fastapi.staticfiles import StaticFiles

    # 挂载素材库目录（新素材系统）
    # follow_symlink=True 允许 StaticFiles 跟随 symlink 访问外部文件
    asset_lib_dir = Path(BASE_DIR) / "素材库"
    if asset_lib_dir.exists():
        app.mount("/static/assetlib", StaticFiles(directory=str(asset_lib_dir), follow_symlink=True), name="素材库")

    # 挂载素材目录（图片预览 - 旧目录兼容）
    assets_dir = Path(BASE_DIR) / "素材"
    if assets_dir.exists():
        app.mount("/static/assets", StaticFiles(directory=str(assets_dir)), name="素材")

    # 挂载输出目录（视频预览）
    output_dir = Path(BASE_DIR) / resolve_path(runtime_config["batch"]["output_dir"])
    if output_dir.exists():
        app.mount("/static/output", StaticFiles(directory=str(output_dir)), name="输出")

    @app.get("/")
    async def serve_index():
        """根路径返回 index.html"""
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        raise HTTPException(status_code=404, detail="前端文件未找到")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """SPA 路由回退：非 API 路径返回 index.html"""
        # 如果是 API 路径，跳过
        if full_path.startswith("api/") or full_path.startswith("ws") or full_path.startswith("static/"):
            raise HTTPException(status_code=404)
        # 尝试匹配静态文件
        file_path = static_dir / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        # SPA 回退
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        raise HTTPException(status_code=404)


_mount_static_files()


# ══════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════

def _get_task_config(
    tts_engine: Optional[str] = None,
    tts_voice: Optional[str] = None,
    subtitle_style: Optional[int] = None,
    subtitle_animation: Optional[str] = None,
    cover_duration: Optional[float] = None,
    subtitle_position: Optional[str] = None,
    subtitle_margin: Optional[int] = None,
) -> dict:
    """根据请求参数构建任务配置
    注意：count（文案句数）、cover、cover_text、cover_subtitle 通过 gen_kwargs 直接传递给
    generate_single_video，不经由此函数，因为它们是生成参数而非配置项。
    """
    import copy
    config = copy.deepcopy(runtime_config)

    if tts_engine:
        config["audio"]["tts_engine"] = tts_engine
    if tts_voice:
        config["audio"]["edge_tts_voice"] = tts_voice
    if subtitle_style is not None:
        config["video"]["subtitle_style"] = subtitle_style
    if subtitle_animation:
        config["video"]["subtitle_animation"] = subtitle_animation
    if subtitle_position:
        config["video"]["subtitle_position"] = subtitle_position
    if subtitle_margin is not None:
        config["video"]["subtitle_margin"] = subtitle_margin
    if cover_duration is not None:
        config["video"]["cover_duration"] = cover_duration

    return config


# ══════════════════════════════════════════════════════
# 统一错误处理
# ══════════════════════════════════════════════════════

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """全局异常处理器"""
    logger.error(f"未处理的异常: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "服务器内部错误", "detail": str(exc)},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """HTTP 异常处理器"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "detail": exc.detail},
    )


# ══════════════════════════════════════════════════════
# 启动入口
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))

    logger.info(f"启动团购视频生成器 API 服务: http://0.0.0.0:{port}")
    logger.info(f"API 文档: http://0.0.0.0:{port}/docs")
    logger.info(f"项目根目录: {BASE_DIR}")

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )
