import asyncio
import os
import re
from pathlib import Path
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta

logger = logging.getLogger("video_maker_web.publish")

publish_scheduler = AsyncIOScheduler()

def init_scheduler():
    try:
        if not publish_scheduler.running:
            publish_scheduler.start()
    except Exception as e:
        logger.error(f"Failed to start publish_scheduler: {e}")

def add_to_publish_schedule(task_id: str, video_path: str, guide_file: str, title: str, run_at: datetime):
    publish_scheduler.add_job(
        publish_video,
        'date',
        run_date=run_at,
        args=[video_path, guide_file, title],
        id=f"publish_{task_id}_{int(run_at.timestamp())}"
    )

def get_publish_jobs():
    jobs = publish_scheduler.get_jobs()
    res = []
    for j in jobs:
        args = j.args
        video_name = Path(args[0]).name if args else "未知视频"
        res.append({
            "id": j.id, 
            "run_at": j.next_run_time.isoformat() if j.next_run_time else None,
            "video_name": video_name
        })
    return res

def delete_publish_job(job_id: str):
    try:
        publish_scheduler.remove_job(job_id)
        return True
    except Exception as e:
        logger.error(f"Failed to remove job {job_id}: {e}")
        return False


def parse_guide_file(guide_path: str):
    """Parse the _发布指南.txt file to extract title, description, and hashtags."""
    if not guide_path or not os.path.exists(guide_path):
        return None, None
    
    content = Path(guide_path).read_text(encoding='utf-8')
    title = ""
    desc = ""
    tags = ""
    
    # Extract title
    title_match = re.search(r'===视频标题===\n(.*?)\n', content, re.DOTALL)
    if title_match:
        title = title_match.group(1).strip()
        
    # Extract description
    desc_match = re.search(r'===视频描述===\n(.*?)\n===', content, re.DOTALL)
    if desc_match:
        desc = desc_match.group(1).strip()
        
    # Extract tags
    tags_match = re.search(r'===话题标签===\n(.*?)\n', content, re.DOTALL)
    if tags_match:
        tags = tags_match.group(1).strip()
        
    full_desc = f"{desc}\n{tags}".strip()
    return title, full_desc

async def publish_video(video_path: str, guide_file: str = None, title: str = None):
    """Call the node script to publish the video."""
    parsed_title, parsed_desc = parse_guide_file(guide_file)
    
    final_title = parsed_title or title or "自动发布视频"
    final_desc = parsed_desc or "使用自动化系统发布"
    
    douyin_dir = str(Path.home() / "Documents" / "douyin-upload-mcp-skill")
    if not os.path.exists(douyin_dir):
        logger.error(f"抖音发布项目不存在: {douyin_dir}")
        return False, "抖音发布项目不存在"
        
    cmd = [
        "node", "src/demo.js",
        "--video", str(video_path),
        "--title", final_title,
        "--description", final_desc
    ]
    
    logger.info(f"开始发布视频: {' '.join(cmd)}")
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=douyin_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await proc.communicate()
        out_str = stdout.decode('utf-8')
        err_str = stderr.decode('utf-8')
        
        if "✅ 视频发布成功" in out_str:
            logger.info("✅ 视频发布成功")
            return True, "发布成功"
        else:
            logger.error(f"发布失败: {out_str} | {err_str}")
            # Try to extract the error reason if possible
            return False, f"发布未检测到成功标志。输出片段: {out_str[-200:] if len(out_str)>200 else out_str}"
            
    except Exception as e:
        logger.exception("发布执行异常")
        return False, str(e)
