import math
from functools import lru_cache
from .adapters import ProcessingError
from .config import settings


DEFAULT_MODEL = 'BAAI/bge-small-zh-v1.5'


@lru_cache(maxsize=1)
def load_model(model_name, cache_dir, dimension):
    try:
        from fastembed import TextEmbedding
    except ImportError:
        raise ProcessingError('LOCAL_EMBEDDING_RUNTIME_MISSING', '尚未安装本机向量运行库，请安装项目依赖。', False) from None
    description = next((model for model in TextEmbedding.list_supported_models() if model['model'] == model_name), None)
    if not description or description['dim'] != dimension:
        raise ProcessingError('AI_EMBEDDING_INVALID', '本机向量模型名称或输出维度不匹配，请检查配置。', False)
    try:
        # 服务运行期间仅加载已下载的权重，输入文字不发送到模型下载站点。
        return TextEmbedding(model_name=model_name, cache_dir=cache_dir, local_files_only=True,
                             threads=2, providers=['CPUExecutionProvider'])
    except Exception:
        raise ProcessingError('LOCAL_EMBEDDING_NOT_READY',
            '本机向量模型尚未准备完成，请运行 scripts/manage.py prepare-embeddings。', False) from None


def embed_local(texts):
    model = load_model(settings.ai_embedding_model, str(settings.data_dir / 'models'), settings.ai_embedding_dimension)
    vectors = [vector.tolist() for vector in model.embed(texts)]
    if len(vectors) != len(texts) or any(len(vector) != settings.ai_embedding_dimension
            or not all(math.isfinite(value) for value in vector) for vector in vectors):
        raise ProcessingError('AI_EMBEDDING_INVALID', '本机向量模型输出维度或数值与配置不一致，未写入索引。', False)
    return vectors


def prepare_model():
    from fastembed import TextEmbedding
    model_name = settings.ai_embedding_model or DEFAULT_MODEL
    model = TextEmbedding(model_name=model_name, cache_dir=str(settings.data_dir / 'models'), threads=2,
                          providers=['CPUExecutionProvider'])
    vectors = [vector.tolist() for vector in model.embed(['如何建立可核验来源的知识库？', '整理资料时保留原文和出处。'])]
    return {'model': model_name, 'dimension': len(vectors[0]), 'sample_count': len(vectors),
            'all_finite': all(math.isfinite(value) for vector in vectors for value in vector),
            'paid_calls': 0, 'storage': 'data/models'}
