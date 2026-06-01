"""
Pexels API 视频素材抓取模块
从 Pexels 免费素材库自动搜索、下载匹配的视频片段
"""
import os
import json
import logging
import asyncio
import aiohttp
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Pexels API 配置
PEXELS_API_URL = "https://api.pexels.com/videos/search"
PEXELS_VIDEO_URL = "https://www.pexels.com/video/{id}/download/"
PEXELS_PHOTO_API_URL = "https://api.pexels.com/v1/search"


class PexelsClient:
    """Pexels API 客户端"""
    
    def __init__(self, api_key: str, download_dir: str = "素材/pexels"):
        self.api_key = api_key
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.headers = {"Authorization": api_key}
    
    async def search_videos(
        self,
        query: str,
        per_page: int = 5,
        orientation: str = "portrait",
        size: str = "medium",
        min_duration: int = 5,
        max_duration: int = 30
    ) -> list[dict]:
        """
        搜索视频素材
        
        Args:
            query: 搜索关键词
            per_page: 每页结果数
            orientation: portrait(竖屏)/landscape(横屏)/square(方形)
            size: large(4K)/medium(FHD)/small(HD)
            min_duration: 最小持续时间(秒)
            max_duration: 最大持续时间(秒)
        
        Returns:
            视频信息列表
        """
        params = {
            "query": query,
            "per_page": per_page,
            "orientation": orientation,
            "size": size,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    PEXELS_API_URL,
                    headers=self.headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"Pexels API 错误: {resp.status}")
                        return []
                    
                    data = await resp.json()
                    videos = []
                    
                    for video in data.get("videos", []):
                        # 过滤时长
                        duration = video.get("duration", 0)
                        if duration < min_duration or duration > max_duration:
                            continue
                        
                        # 找到最合适的视频文件
                        video_file = self._select_best_file(
                            video.get("video_files", []),
                            orientation
                        )
                        
                        if video_file:
                            videos.append({
                                "id": video["id"],
                                "url": video_file["link"],
                                "width": video_file.get("width", 0),
                                "height": video_file.get("height", 0),
                                "duration": duration,
                                "quality": video_file.get("quality", ""),
                                "fps": video_file.get("fps", 0),
                            })
                    
                    logger.info(f"Pexels搜索 '{query}': 找到 {len(videos)} 个视频")
                    return videos
        
        except Exception as e:
            logger.error(f"Pexels搜索失败: {e}")
            return []
    
    def _select_best_file(self, files: list, orientation: str) -> Optional[dict]:
        """选择最合适的视频文件"""
        if not files:
            return None
        
        # 优先级：高清 > 标清 > 低清
        priority = {"hd": 3, "sd": 2, "hls": 1}
        
        # 竖屏优先选择 1080x1920 或 720x1280
        # 横屏优先选择 1920x1080 或 1280x720
        target_ratio = 9/16 if orientation == "portrait" else 16/9
        
        scored_files = []
        for f in files:
            if f.get("file_type") != "video/mp4":
                continue
            
            w, h = f.get("width", 0), f.get("height", 0)
            if w == 0 or h == 0:
                continue
            
            ratio = w / h
            ratio_score = 1 - abs(ratio - target_ratio) / target_ratio
            quality_score = priority.get(f.get("quality", ""), 0)
            resolution_score = (w * h) / (1920 * 1080)  # 归一化到1080p
            
            total_score = ratio_score * 0.4 + quality_score * 0.3 + resolution_score * 0.3
            scored_files.append((total_score, f))
        
        if not scored_files:
            return files[0] if files else None
        
        scored_files.sort(key=lambda x: x[0], reverse=True)
        return scored_files[0][1]
    
    async def download_video(self, video_info: dict, filename: str = None) -> Optional[str]:
        """
        下载视频到本地
        
        Args:
            video_info: 视频信息（来自search_videos）
            filename: 自定义文件名
        
        Returns:
            下载后的本地文件路径
        """
        if not filename:
            filename = f"pexels_{video_info['id']}.mp4"
        
        filepath = self.download_dir / filename
        
        # 如果已下载，直接返回
        if filepath.exists():
            logger.info(f"视频已存在: {filepath}")
            return str(filepath)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    video_info["url"],
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"下载失败: {resp.status}")
                        return None

                    # 使用临时文件写入，防止下载中断导致残留不完整文件
                    tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")
                    try:
                        with open(tmp_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                f.write(chunk)
                        # 下载完成后原子重命名（跨文件系统时回退到 shutil.move）
                        try:
                            tmp_path.rename(filepath)
                        except OSError:
                            import shutil
                            shutil.move(str(tmp_path), str(filepath))
                    except Exception:
                        # 清理不完整的临时文件
                        if tmp_path.exists():
                            tmp_path.unlink()
                        raise
                    
                    logger.info(f"下载完成: {filepath}")
                    return str(filepath)
        
        except Exception as e:
            logger.error(f"下载失败: {e}")
            return None
    
    async def search_and_download(
        self,
        query: str,
        count: int = 3,
        orientation: str = "portrait",
        **kwargs
    ) -> list[str]:
        """
        搜索并下载视频（一站式）
        
        Args:
            query: 搜索关键词
            count: 下载数量
            orientation: 竖屏/横屏
        
        Returns:
            下载的本地文件路径列表
        """
        videos = await self.search_videos(
            query=query,
            per_page=count * 2,  # 多搜一些，过滤后可能不够
            orientation=orientation,
            **kwargs
        )
        
        if not videos:
            logger.warning(f"未找到匹配的视频: {query}")
            return []
        
        # 下载指定数量
        downloaded = []
        for video in videos[:count]:
            filename = f"pexels_{query}_{video['id']}.mp4"
            path = await self.download_video(video, filename)
            if path:
                downloaded.append(path)
        
        return downloaded

    async def search_photos(
        self,
        query: str,
        per_page: int = 15,
        orientation: str = "portrait",
    ) -> list[dict]:
        """搜索图片素材"""
        params = {
            "query": query,
            "per_page": per_page,
            "orientation": orientation,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    PEXELS_PHOTO_API_URL,
                    headers=self.headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"Pexels Photo API 错误: {resp.status}")
                        return []
                    
                    data = await resp.json()
                    photos = []
                    
                    for photo in data.get("photos", []):
                        # Pexels 返回的 src 对象包含 original, large2x, large, medium, small, portrait, landscape, tiny
                        url = photo.get("src", {}).get("large2x") or photo.get("src", {}).get("large") or photo.get("src", {}).get("original")
                        if url:
                            photos.append({
                                "id": photo["id"],
                                "url": url,
                                "width": photo.get("width", 0),
                                "height": photo.get("height", 0),
                            })
                    
                    logger.info(f"Pexels图片搜索 '{query}': 找到 {len(photos)} 张图片")
                    return photos
        except Exception as e:
            logger.error(f"Pexels图片搜索失败: {e}")
            return []

    async def download_photo(self, photo_info: dict, filename: str = None) -> Optional[str]:
        """下载图片到本地"""
        if not filename:
            filename = f"pexels_photo_{photo_info['id']}.jpg"
        
        filepath = self.download_dir / filename
        
        if filepath.exists():
            return str(filepath)
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(photo_info["url"], timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        return None
                    tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")
                    try:
                        with open(tmp_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                f.write(chunk)
                        try:
                            tmp_path.rename(filepath)
                        except OSError:
                            import shutil
                            shutil.move(str(tmp_path), str(filepath))
                    except Exception:
                        if tmp_path.exists():
                            tmp_path.unlink()
                        raise
                    return str(filepath)
        except Exception as e:
            logger.error(f"图片下载失败: {e}")
            return None

    async def search_and_download_photos(
        self,
        query: str,
        count: int = 3,
        orientation: str = "portrait",
        **kwargs
    ) -> list[str]:
        """搜索并下载图片（一站式）"""
        photos = await self.search_photos(query=query, per_page=count * 2, orientation=orientation)
        if not photos:
            return []
        
        downloaded = []
        for photo in photos[:count]:
            filename = f"pexels_{query}_{photo['id']}.jpg"
            path = await self.download_photo(photo, filename)
            if path:
                downloaded.append(path)
        return downloaded


async def fetch_pexels_videos(
    keywords: list[str],
    count_per_keyword: int = 2,
    orientation: str = "portrait",
    api_key: str = None,
    download_dir: str = "素材/pexels"
) -> list[str]:
    """
    根据关键词列表批量获取视频素材
    
    Args:
        keywords: 关键词列表
        count_per_keyword: 每个关键词下载的视频数
        orientation: 竖屏/横屏
        api_key: Pexels API Key
        download_dir: 下载目录
    
    Returns:
        所有下载的视频路径
    """
    if not api_key:
        logger.warning("未配置 Pexels API Key，跳过视频素材抓取")
        return []
    
    client = PexelsClient(api_key, download_dir)
    all_videos = []
    
    for keyword in keywords:
        videos = await client.search_and_download(
            query=keyword,
            count=count_per_keyword,
            orientation=orientation
        )
        all_videos.extend(videos)
    
    logger.info(f"共下载 {len(all_videos)} 个视频素材")
    return all_videos

async def fetch_pexels_photos(
    keywords: list[str],
    count_per_keyword: int = 2,
    orientation: str = "portrait",
    api_key: str = None,
    download_dir: str = "素材/pexels"
) -> list[str]:
    """批量获取图片素材"""
    if not api_key:
        return []
    
    client = PexelsClient(api_key, download_dir)
    all_photos = []
    
    for keyword in keywords:
        photos = await client.search_and_download_photos(
            query=keyword,
            count=count_per_keyword,
            orientation=orientation
        )
        all_photos.extend(photos)
    
    logger.info(f"共下载 {len(all_photos)} 张图片素材")
    return all_photos


# 同步版本（供非async环境使用）
def fetch_pexels_videos_sync(
    keywords: list[str],
    count_per_keyword: int = 2,
    orientation: str = "portrait",
    api_key: str = None,
    download_dir: str = "素材/pexels"
) -> list[str]:
    """同步版本的视频素材获取"""
    return asyncio.run(fetch_pexels_videos(
        keywords=keywords,
        count_per_keyword=count_per_keyword,
        orientation=orientation,
        api_key=api_key,
        download_dir=download_dir
    ))

def fetch_pexels_photos_sync(
    keywords: list[str],
    count_per_keyword: int = 2,
    orientation: str = "portrait",
    api_key: str = None,
    download_dir: str = "素材/pexels"
) -> list[str]:
    """同步版本的图片素材获取"""
    return asyncio.run(fetch_pexels_photos(
        keywords=keywords,
        count_per_keyword=count_per_keyword,
        orientation=orientation,
        api_key=api_key,
        download_dir=download_dir
    ))


if __name__ == "__main__":
    # 测试
    import sys
    
    logging.basicConfig(level=logging.INFO)
    
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        print("请设置环境变量 PEXELS_API_KEY")
        sys.exit(1)
    
    videos = asyncio.run(fetch_pexels_videos(
        keywords=["pet boarding", "dog playing", "cute dog"],
        count_per_keyword=2,
        api_key=api_key
    ))
    
    print(f"\n下载的视频:")
    for v in videos:
        print(f"  - {v}")
