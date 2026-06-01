"""
背景音乐自动匹配模块
根据视频内容/关键词自动选择合适的背景音乐
"""
import os
import random
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 音乐风格映射表
STYLE_MAPPING = {
    # 情感关键词 -> 音乐风格
    "温馨": "warm",
    "可爱": "cute",
    "活泼": "upbeat",
    "快乐": "happy",
    "轻松": "relaxing",
    "治愈": "healing",
    "感动": "emotional",
    "专业": "professional",
    "高端": "elegant",
    "日常": "casual",
    "节日": "festive",
    "夏天": "summer",
    "冬天": "winter",
    "春天": "spring",
    "秋天": "autumn",
    "宠物": "pet",
    "狗狗": "dog",
    "猫咪": "cat",
    "寄养": "boarding",
    "游泳": "swimming",
    "遛狗": "walking",
    "儿童节": "children",
    "端午节": "dragon_boat",
    "春节": "chinese_new_year",
    "中秋": "mid_autumn",
}

# 风格 -> BPM范围
STYLE_BPM = {
    "warm": (80, 100),
    "cute": (100, 120),
    "upbeat": (120, 140),
    "happy": (110, 130),
    "relaxing": (70, 90),
    "healing": (60, 80),
    "emotional": (70, 90),
    "professional": (90, 110),
    "elegant": (80, 100),
    "casual": (90, 110),
    "festive": (120, 140),
    "summer": (110, 130),
    "winter": (80, 100),
    "spring": (100, 120),
    "autumn": (90, 110),
    "pet": (100, 120),
    "dog": (100, 120),
    "cat": (80, 100),
    "boarding": (90, 110),
    "swimming": (110, 130),
    "walking": (100, 120),
    "children": (120, 140),
    "dragon_boat": (100, 120),
    "chinese_new_year": (120, 140),
    "mid_autumn": (80, 100),
    "default": (90, 110),
}


class BGMSelector:
    """背景音乐选择器"""
    
    def __init__(self, music_dir: str = "音乐"):
        self.music_dir = Path(music_dir)
        self.music_index = self._build_index()
    
    def _build_index(self) -> dict:
        """构建音乐索引"""
        index = {}
        
        if not self.music_dir.exists():
            logger.warning(f"音乐目录不存在: {self.music_dir}")
            return index
        
        for file in self.music_dir.glob("**/*"):
            if file.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".ogg"):
                # 从文件名提取风格标签
                name = file.stem.lower()
                style = self._detect_style(name)
                
                if style not in index:
                    index[style] = []
                index[style].append(str(file))
        
        logger.info(f"音乐索引: {len(index)} 种风格, "
                    f"{sum(len(v) for v in index.values())} 首音乐")
        return index
    
    def _detect_style(self, filename: str) -> str:
        """从文件名检测音乐风格"""
        for keyword, style in STYLE_MAPPING.items():
            if keyword in filename:
                return style
        return "default"
    
    def select(
        self,
        topic: str = "",
        keywords: list[str] = None,
        duration: float = 0,
        exclude: list[str] = None
    ) -> Optional[str]:
        """
        根据主题/关键词选择背景音乐
        
        Args:
            topic: 视频主题
            keywords: 关键词列表
            duration: 视频时长(秒)，用于选择合适长度的音乐
            exclude: 排除的音乐文件列表
        
        Returns:
            选中的音乐文件路径
        """
        if not self.music_index:
            logger.warning("音乐库为空")
            return None
        
        exclude = exclude or []
        
        # 分析主题，确定风格
        target_styles = self._analyze_topic(topic, keywords or [])
        
        # 收集候选音乐
        candidates = []
        for style in target_styles:
            if style in self.music_index:
                candidates.extend(self.music_index[style])
        
        # 如果没有匹配的风格，使用默认
        if not candidates:
            candidates = self.music_index.get("default", [])
        
        # 如果还是没有，使用所有音乐
        if not candidates:
            candidates = [f for files in self.music_index.values() for f in files]
        
        # 排除已使用的
        candidates = [c for c in candidates if c not in exclude]
        
        if not candidates:
            logger.warning("没有可用的背景音乐")
            return None
        
        # 随机选择
        selected = random.choice(candidates)
        logger.info(f"选择背景音乐: {Path(selected).name} (风格: {target_styles})")
        return selected
    
    def _analyze_topic(self, topic: str, keywords: list[str]) -> list[str]:
        """分析主题，返回匹配的风格列表"""
        styles = []
        
        # 合并主题和关键词
        texts = [topic] + keywords
        combined = " ".join(texts).lower()
        
        # 匹配风格
        for keyword, style in STYLE_MAPPING.items():
            if keyword in combined:
                styles.append(style)
        
        # 去重，保持顺序
        seen = set()
        unique_styles = []
        for s in styles:
            if s not in seen:
                seen.add(s)
                unique_styles.append(s)
        
        # 如果没有匹配，返回默认
        if not unique_styles:
            unique_styles = ["default"]
        
        return unique_styles
    
    def get_random(self, style: str = None) -> Optional[str]:
        """随机获取一首音乐"""
        if style and style in self.music_index:
            pool = self.music_index[style]
        else:
            pool = [f for files in self.music_index.values() for f in files]
        
        return random.choice(pool) if pool else None
    
    def list_styles(self) -> list[str]:
        """列出所有可用的音乐风格"""
        return list(self.music_index.keys())
    
    def list_music(self, style: str = None) -> list[str]:
        """列出所有音乐文件"""
        if style:
            return self.music_index.get(style, [])
        return [f for files in self.music_index.values() for f in files]


def select_bgm(
    topic: str = "",
    keywords: list[str] = None,
    music_dir: str = "音乐",
    duration: float = 0
) -> Optional[str]:
    """
    便捷函数：选择背景音乐
    
    Args:
        topic: 视频主题
        keywords: 关键词列表
        music_dir: 音乐目录
        duration: 视频时长
    
    Returns:
        选中的音乐文件路径
    """
    selector = BGMSelector(music_dir)
    return selector.select(topic=topic, keywords=keywords, duration=duration)


def auto_match_bgm(
    topic: str,
    music_dir: str = "音乐",
    video_duration: float = 0
) -> Optional[str]:
    """
    自动匹配背景音乐（根据主题分析）
    
    Args:
        topic: 视频主题
        music_dir: 音乐目录
        video_duration: 视频时长
    
    Returns:
        匹配的音乐文件路径
    """
    # 从主题中提取关键词
    keywords = []
    
    # 简单的关键词提取
    for word in ["宠物", "狗狗", "猫咪", "寄养", "游泳", "遛狗", 
                 "儿童节", "端午节", "春节", "中秋", "夏天", "冬天",
                 "温馨", "可爱", "活泼", "快乐", "轻松", "治愈"]:
        if word in topic:
            keywords.append(word)
    
    return select_bgm(
        topic=topic,
        keywords=keywords,
        music_dir=music_dir,
        duration=video_duration
    )


if __name__ == "__main__":
    # 测试
    logging.basicConfig(level=logging.INFO)
    
    selector = BGMSelector("音乐")
    
    print("可用音乐风格:")
    for style in selector.list_styles():
        music = selector.list_music(style)
        print(f"  {style}: {len(music)} 首")
    
    print("\n测试选择:")
    topics = [
        "宠物寄养哪家好",
        "狗狗游泳注意事项",
        "儿童节带娃出游",
        "端午节宠物寄养攻略",
    ]
    
    for topic in topics:
        bgm = selector.select(topic=topic)
        print(f"  '{topic}' -> {Path(bgm).name if bgm else '无'}")
