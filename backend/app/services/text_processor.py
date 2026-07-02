"""
文本处理服务
"""

from typing import List, Optional
from ..utils.file_parser import FileParser, split_text_into_chunks

try:
    # CHUNK-1：本文件不拥有 config.py，仅经 getattr 读取已配置的默认切块大小（缺失即降级）。
    from ..config import Config as _Config
except Exception:  # pragma: no cover - 配置导入失败时退回内置默认值
    _Config = None


class TextProcessor:
    """文本处理器"""

    @staticmethod
    def extract_from_files(file_paths: List[str]) -> str:
        """从多个文件提取文本"""
        return FileParser.extract_from_multiple(file_paths)

    @staticmethod
    def split_text(
        text: str,
        chunk_size: Optional[int] = None,
        overlap: Optional[int] = None
    ) -> List[str]:
        """
        分割文本（句子/段落边界感知；底层 split_text_into_chunks 已在句末/空行处优先切分）。

        CHUNK-1：``chunk_size`` / ``overlap`` 缺省（None）时从 Config.DEFAULT_CHUNK_SIZE /
        DEFAULT_CHUNK_OVERLAP 解析，从而自动"honor 更大的切块尺寸"——这是把 ~400 个微 episode
        压成 ~80 个的核心杠杆（图谱阶段最大提速点）。现有调用方都显式传入尺寸，故对它们逐字节不变；
        本改动只让未显式传参的调用方拿到已配置（更大）的默认值。配置缺失时退回历史默认 500/50。

        Args:
            text: 原始文本
            chunk_size: 块大小（None → Config.DEFAULT_CHUNK_SIZE → 500）
            overlap: 重叠大小（None → Config.DEFAULT_CHUNK_OVERLAP → 50）

        Returns:
            文本块列表
        """
        if chunk_size is None:
            chunk_size = int(getattr(_Config, "DEFAULT_CHUNK_SIZE", 500) or 500)
        if overlap is None:
            overlap = int(getattr(_Config, "DEFAULT_CHUNK_OVERLAP", 50) or 50)
        return split_text_into_chunks(text, chunk_size, overlap)
    
    @staticmethod
    def preprocess_text(text: str) -> str:
        """
        预处理文本
        - 移除多余空白
        - 标准化换行
        
        Args:
            text: 原始文本
            
        Returns:
            处理后的文本
        """
        import re
        
        # 标准化换行
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # 移除连续空行（保留最多两个换行）
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # 移除行首行尾空白
        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(lines)
        
        return text.strip()
    
    @staticmethod
    def get_text_stats(text: str) -> dict:
        """获取文本统计信息"""
        return {
            "total_chars": len(text),
            "total_lines": text.count('\n') + 1,
            "total_words": len(text.split()),
        }

