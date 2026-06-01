import os
import logging
from PIL import Image

logger = logging.getLogger(__name__)

_clip_model = None
_clip_processor = None

def get_clip():
    global _clip_model, _clip_processor
    if _clip_model is None:
        try:
            from transformers import CLIPProcessor, CLIPModel
            import warnings
            warnings.filterwarnings("ignore")
            logger.info("正在尝试加载 CLIP 零样本分类模型...")
            try:
                # 尝试强制使用本地缓存，避免由于网络被墙导致死等挂起
                _clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32", local_files_only=True)
                _clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32", local_files_only=True)
            except Exception:
                logger.warning("本地未发现 CLIP 模型缓存。如果是首次运行，请确保网络能连通 HuggingFace。")
                # 这里我们直接抛出异常，让外层捕获并跳过 AI 筛查，避免在无网络环境死等
                raise RuntimeError("CLIP 模型需要手动下载或需要代理网络。")
            logger.info("CLIP 模型加载完成！")
        except Exception as e:
            logger.warning(f"CLIP 模型加载失败: {e}")
            _clip_model = False
            _clip_processor = False
    return _clip_model, _clip_processor

def is_image_safe(img_path: str) -> bool:
    """
    检查图片是否安全，如果检测到受伤/生病的动物，返回 False，否则返回 True
    """
    try:
        model, processor = get_clip()
        if not model:
            return True  # 降级：如果模型不可用，默认全部安全
        
        image = Image.open(img_path).convert("RGB")
        # 定义需要对比的分类文本
        # 0: 安全的/正常的
        # 1: 负面的/受伤的
        labels = ["a healthy dog or cat or pet", "an injured, sick, or bleeding animal or dog"]
        
        inputs = processor(text=labels, images=image, return_tensors="pt", padding=True)
        outputs = model(**inputs)
        
        # 获取图像-文本相似度的 logits 并使用 softmax 归一化为概率
        logits_per_image = outputs.logits_per_image
        probs = logits_per_image.softmax(dim=1).detach().numpy()[0]
        
        safe_prob = probs[0]
        injured_prob = probs[1]
        
        # 如果受伤的概率明显大于健康的，或者超过特定阈值（比如 40%），则认为不安全
        if injured_prob > 0.4 and injured_prob > safe_prob:
            logger.info(f"🚫 [AI 过滤] 图片不合规 ({img_path}): 检测到疑似受伤/生病特征 (置信度 {injured_prob:.2f})")
            return False
            
        return True
    except Exception as e:
        logger.warning(f"图片 AI 安全检查失败 {img_path}: {e}")
        return True

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1:
        path = sys.argv[1]
        print(f"[{path}] Safe? {is_image_safe(path)}")
