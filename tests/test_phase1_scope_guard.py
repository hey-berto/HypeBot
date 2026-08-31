from pathlib import Path


def test_no_llm_wallet_or_live_order_execution_code_exists():
    source = "\n".join(path.read_text(encoding="utf-8") for path in Path("src").rglob("*.py")).lower()
    forbidden = ("import openai", "from openai", "private_key", "private key",
                 "from hyperliquid.exchange", ".order(", ".market_open(")
    assert not [needle for needle in forbidden if needle in source]
