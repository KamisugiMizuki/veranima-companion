"""embedding 专用 base_url/api_key 覆盖的行为检查（安卓化远程 embedding 入口）。"""
from veranima.memory.embedding import make_provider, OpenAIEmbedProvider


def test_openai_spec_uses_embedding_override():
    p = make_provider(
        {"embedding_model": "openai:bge-m3-large",
         "embedding_base_url": "https://emb.example/v1",
         "embedding_api_key": "sk-emb"},
        {"base_url": "https://llm.example/v1", "api_key": "sk-llm"},
    )
    assert isinstance(p, OpenAIEmbedProvider)
    assert p.base_url == "https://emb.example/v1"
    assert p.api_key == "sk-emb"
    assert p.model == "bge-m3-large"


def test_openai_spec_falls_back_to_llm_section():
    p = make_provider(
        {"embedding_model": "openai:m"},
        {"base_url": "https://llm.example/v1", "api_key": "sk-llm"},
    )
    assert p.base_url == "https://llm.example/v1"
    assert p.api_key == "sk-llm"
