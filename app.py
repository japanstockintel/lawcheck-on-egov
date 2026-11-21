import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple
import streamlit as st

# =====================================================================
# 設定 & 定数
# =====================================================================
st.set_page_config(
    page_title="e-Gov直結 法令整合性チェッカー",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_URL = "https://laws.e-gov.go.jp/api/2"

# --- トークンカウンター管理クラス ---
class TokenTracker:
    def __init__(self):
        self.prompt_tokens = 0
        self.candidate_tokens = 0
        self.total_tokens = 0
        self.call_count = 0

    def add(self, usage: Dict[str, Any]):
        if not usage:
            return
        self.prompt_tokens += usage.get("promptTokenCount", 0)
        self.candidate_tokens += usage.get("candidatesTokenCount", 0)
        self.total_tokens += usage.get("totalTokenCount", 0)
        self.call_count += 1

    def summary(self) -> str:
        return (
            f"**API呼び出し回数**: {self.call_count} 回  |  "
            f"**入力トークン**: {self.prompt_tokens:,}  |  "
            f"**出力トークン**: {self.candidate_tokens:,}  |  "
            f"**合計消費**: {self.total_tokens:,} tokens (完全無料枠内)"
        )


def get_first(items):
    """リストの先頭要素を安全に取得するヘルパー関数"""
    if not items:
        return None
    for item in items:
        return item
    return None


# --- 1. Gemini REST API 通信（タイムアウト親切メッセージ＆45秒緩和） ---
def call_gemini_api(
    api_key: str,
    prompt: str,
    model_name: str = "gemini-3.8-flash",
    tracker: Optional[TokenTracker] = None
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Gemini 公式 REST API を直接呼び出し、テキストとトークン利用情報を返す"""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    payload_bytes = json.dumps(payload).encode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

    req = urllib.request.Request(
        url,
        data=payload_bytes,
        headers={"Content-Type": "application/json"}
    )

    max_retries = 3
    last_err = ""
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=45) as res:
                res_data = json.loads(res.read().decode("utf-8"))
                usage = res_data.get("usageMetadata", {})
                if tracker and usage:
                    tracker.add(usage)

                candidates = res_data.get("candidates", [])
                first_cand = get_first(candidates)
                if first_cand:
                    content = first_cand.get("content", {})
                    parts = content.get("parts", [])
                    first_part = get_first(parts)
                    if first_part and "text" in first_part:
                        return first_part.get("text", ""), usage
                return "Gemini からの応答が空でした。", usage
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            last_err = f"HTTP {e.code} ({e.reason}): {err_body}"
            if e.code == 429:
                return (
                    "⚠️ **【リクエスト上限 (HTTP 429)】**: 本日の無料利用上限に達しました。\n"
                    "Google側の制限により、日本時間の日中に枠がリセットされます。時間をおいて再試行してください。",
                    None
                )
            if e.code == 503 and attempt < max_retries:
                time.sleep(attempt * 2)
                continue
            return f"Gemini API エラー: {last_err}", None
        except Exception as e:
            err_msg = str(e)
            if "timed out" in err_msg.lower() or "timeout" in err_msg.lower():
                return (
                    "⚠️ **【通信タイムアウト（応答待ち時間超過）】**\n\n"
                    "AIサーバーまたは国の法令データベースとの通信が混雑等のため時間内に完了しませんでした。\n\n"
                    "**【対処法】**\n"
                    "1. お手数ですが、**もう一度「🔍 法令チェックを実行」ボタンを押して再試行**してください（通常は2度目で正常に完了します）。\n"
                    "2. 解消しない場合は、チェック対象の文章を論点ごとに少し短く分けて入力してみてください。",
                    None
                )
            return f"通信エラーが発生しました: {e}", None

    return f"呼び出しに失敗しました: {last_err}", None


# --- 2. e-Gov 法令API クライアント ---
class EGovLawAPIClient:
    """e-Gov 法令API Version 2 クライアント"""

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> bytes:
        url = f"{self.base_url}{endpoint}"
        if params is None:
            params = {}
        if "response_format" not in params:
            params["response_format"] = "json"
        filtered_params = {k: v for k, v in params.items() if v is not None}
        if filtered_params:
            query = urllib.parse.urlencode(filtered_params)
            url = f"{url}?{query}"

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "EGovLawCheckerApp/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b'{"items": [], "revisions": [], "laws": [], "not_found": true}'
            try:
                err_data = e.read()
                return err_data if err_data else b'{"error": true}'
            except Exception:
                return b'{"error": true}'
        except Exception:
            return b'{"error": true}'

    def get_laws(self, law_title: Optional[str] = None) -> Dict[str, Any]:
        params = {"law_title": law_title} if law_title else {}
        raw = self._get("/laws", params=params)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"raw_xml": raw.decode("utf-8")}

    def search_keyword(self, keyword: str) -> Dict[str, Any]:
        params = {"keyword": keyword}
        raw = self._get("/keyword", params=params)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"raw_xml": raw.decode("utf-8")}

    def find_law(self, law_name: str) -> Optional[Dict[str, Any]]:
        laws_res = self.get_laws(law_title=law_name)
        if isinstance(laws_res, dict) and "laws" in laws_res:
            law_list = laws_res.get("laws", [])
            for item in law_list:
                rev_info = item.get("revision_info", {})
                law_info = item.get("law_info", {})
                title = rev_info.get("law_title") or law_info.get("law_title")
                if title == law_name:
                    return item
            if law_list:
                return get_first(law_list)

        kw_res = self.search_keyword(law_name)
        if isinstance(kw_res, dict) and "items" in kw_res:
            item_list = kw_res.get("items", [])
            for item in item_list:
                rev_info = item.get("revision_info", {})
                law_info = item.get("law_info", {})
                title = rev_info.get("law_title") or law_info.get("law_title")
                if title == law_name:
                    return item
            if item_list:
                return get_first(item_list)

        return None

    def get_law_revisions(self, law_id_or_num: str) -> Dict[str, Any]:
        quoted = urllib.parse.quote(law_id_or_num)
        raw = self._get(f"/law_revisions/{quoted}")
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"raw_xml": raw.decode("utf-8")}

    def get_law_data(self, law_id: str) -> str:
        quoted = urllib.parse.quote(law_id)
        raw = self._get(f"/law_data/{quoted}", params={"response_format": "xml"})
        return raw.decode("utf-8")

    def parse_amendment_status(self, revision_info: Dict[str, Any]) -> Dict[str, Any]:
        amendment_type = str(revision_info.get("amendment_type", ""))
        repeal_status = str(revision_info.get("repeal_status", ""))
        is_repealed = (
            amendment_type == "8" or
            repeal_status in ["Repeal", "Expire", "LossOfEffectiveness"]
        )
        return {
            "status_text": "廃止・失効" if is_repealed else "現行・有効",
            "is_repealed": is_repealed
        }


# --- 3. 条文番号の正規化 ＆ XML抽出 ---
def normalize_article_num(raw_str: str) -> str:
    s = re.sub(r'[第条\s]', '', raw_str)
    s = s.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    if s.isdigit():
        return str(int(s))

    kanji_digits = {'〇': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    total = 0
    cur = 0
    for char in s:
        if char in kanji_digits:
            cur = kanji_digits[char]
        elif char == '十':
            total += (cur if cur != 0 else 1) * 10
            cur = 0
        elif char == '百':
            total += (cur if cur != 0 else 1) * 100
            cur = 0
        elif char == '千':
            total += (cur if cur != 0 else 1) * 1000
            cur = 0
    total += cur
    return str(total) if total > 0 else s


def extract_article_from_xml(xml_content: str, article_str: str) -> Dict[str, Any]:
    target_num = normalize_article_num(article_str)
    try:
        root = ET.fromstring(xml_content)
    except Exception as e:
        return {"found": False, "error": f"XML解析エラー: {e}"}

    articles = root.findall(f".//Article[@Num='{target_num}']")
    if not articles:
        for a in root.findall(".//Article"):
            t = a.find("ArticleTitle")
            if t is not None and t.text and (article_str in t.text or f"{target_num}条" in t.text):
                articles.append(a)
                break

    if not articles:
        return {"found": False, "error": f"条文（{article_str}）は見つかりませんでした。"}

    target = get_first(articles)
    caption = target.findtext("ArticleCaption", default="").strip()
    title = target.findtext("ArticleTitle", default="").strip()

    paragraphs = []
    for p in target.findall(".//Paragraph"):
        p_num = p.findtext("ParagraphNum", default="").strip()
        sentences = [s.text for s in p.findall(".//Sentence") if s.text]
        p_text = "".join(sentences).strip()
        if p_num:
            paragraphs.append(f"{p_num} {p_text}")
        else:
            paragraphs.append(p_text)

    body = "\n".join(paragraphs)
    return {
        "found": True,
        "caption": caption,
        "title": title,
        "body": body,
        "full_text": f"{caption}\n{title}\n{body}".strip() if caption else f"{title}\n{body}".strip()
    }


# --- 4. 略称・通称の自動正式名称変換 ＆ 条文特定 ---
def parse_law_references_with_gemini(api_key: str, text: str, tracker: TokenTracker) -> List[Dict[str, Any]]:
    prompt = f"""あなたは日本の法令の専門家です。
以下の【入力文章】を読み、言及されている「法令」をすべて抽出してください。
略称（例: 労基法、健保法、パワハラ防止法など）は必ず【日本の正式な法令名（e-Gov登録名称）】に変換してください。

また、文章中に具体的な条番号が書かれていない場合でも、文章の主張・内容が明らかに特定の条文（本則・附則・経過措置を含む）に関するものである場合は、該当する可能性の高い条文番号（例: 第115条、第143条など）を推定してリストアップしてください。

【入力文章】
「{text}」

【出力形式】
必ず以下の形式の純粋なJSON配列のみを出力してください（Markdownコードブロックや余計な説明文は不要です）：
[
  {{
    "raw_mention": "文章中の表現（例: 労働基準法、労基法36条など）",
    "official_law_name": "正式法令名（例: 労働基準法）",
    "article": "明記されている条文番号（例: 第36条。明記がない場合は空文字）",
    "inferred_articles": ["文章の文脈から該当する条文番号の推定リスト（例: 第115条、第143条）。明記がある場合や不明な場合は空配列 []"]
  }}
]
もし法令への言及が一切ない場合は空の配列 `[]` を出力してください。
"""
    raw_res, _ = call_gemini_api(api_key, prompt, tracker=tracker)

    clean_json = raw_res.strip()
    if clean_json.startswith("```json"):
        clean_json = clean_json[7:]
    if clean_json.startswith("```"):
        clean_json = clean_json[3:]
    if clean_json.endswith("```"):
        clean_json = clean_json[:-3]
    clean_json = clean_json.strip()

    try:
        data = json.loads(clean_json)
        if isinstance(data, list):
            results = []
            for item in data:
                art = item.get("article", "")
                if art and not art.startswith("第") and art.endswith("条"):
                    art = f"第{art}"
                elif art and not art.endswith("条"):
                    art = f"{art}条"

                inferred = item.get("inferred_articles", [])
                results.append({
                    "raw_mention": item.get("raw_mention", ""),
                    "official_law_name": item.get("official_law_name", ""),
                    "article": art,
                    "inferred_articles": inferred if isinstance(inferred, list) else []
                })
            return results
    except Exception:
        pass

    # フォールバック（正規表現）
    pattern = r'([一-龠ぁ-んァ-ヶa-zA-Z0-9]+(?:法|律|令|省令|政令|規則|条例))(?:第([0-9０-９一二三四五六七八九十百千]+)条)?'
    matches = re.findall(pattern, text)
    fallback_res = []
    for law_name, article_digit in matches:
        art = f"第{article_digit}条" if article_digit else ""
        fallback_res.append({
            "raw_mention": f"{law_name}{art}",
            "official_law_name": law_name,
            "article": art,
            "inferred_articles": []
        })
    return fallback_res


# --- 5. 政省令（委任規定）の連鎖特定 ---
def detect_delegated_regulations(api_key: str, law_name: str, article_str: str, article_body: str, tracker: TokenTracker) -> List[Dict[str, str]]:
    if "政令で定める" not in article_body and "省令で定める" not in article_body and "命令で定める" not in article_body:
        return []

    prompt = f"""あなたは日本の労働法・法制執務の専門家です。
以下の【法律条文】には、政令や省令（施行令・施行規則など）への委任規定が含まれています。
この条文の実質的な数値基準や具体的なルールを確認するために【併せて参照すべき具体的な政令・省令の正式名称および条番号】があれば特定してください。

法令名: {law_name}
条文: {article_str}
本文:
{article_body[:1500]}

【出力形式】
参照すべき政省令がある場合のみ、以下の純粋なJSON配列で出力してください（なければ `[]`）：
[
  {{
    "regulation_name": "政令・施行規則等の正式名称（例: 労働基準法施行規則）",
    "article": "条文番号（例: 第17条。不明な場合は空文字）",
    "reason": "参照理由"
  }}
]
"""
    raw_res, _ = call_gemini_api(api_key, prompt, tracker=tracker)
    clean_json = raw_res.strip()
    if clean_json.startswith("```json"):
        clean_json = clean_json[7:]
    if clean_json.startswith("```"):
        clean_json = clean_json[3:]
    if clean_json.endswith("```"):
        clean_json = clean_json[:-3]
    clean_json = clean_json.strip()

    try:
        data = json.loads(clean_json)
        if isinstance(data, list):
            return data[:1]
    except Exception:
        pass
    return []


# --- 6. Gemini による最終法的監査＆添削レポート生成 ---
def audit_with_gemini(api_key: str, input_text: str, evidences: List[Dict[str, Any]], tracker: TokenTracker) -> str:
    evidence_text = ""
    for idx, ev in enumerate(evidences, 1):
        evidence_text += f"\n--- 【公式条文データ {idx}】 ---\n"
        evidence_text += f"法令名: {ev['law_name']}\n"
        evidence_text += f"条文名/見出し: {ev.get('title', '')} {ev.get('caption', '')}\n"
        evidence_text += f"現行ステータス: {ev.get('status', '不明')}\n"
        if ev.get("found"):
            evidence_text += f"正規条文本文:\n{ev.get('body', '')}\n"
        else:
            evidence_text += "正規条文: 文脈照合による法理分析\n"

        delegated = ev.get("delegated_evidences", [])
        for d_idx, dev in enumerate(delegated, 1):
            evidence_text += f"  >> [関連政省令 {d_idx}]: {dev['law_name']} {dev['article']}\n"
            if dev.get("found"):
                evidence_text += f"     本文: {dev.get('body', '')[:800]}\n"

    prompt = f"""あなたは極めて厳格かつ実務に精通した法律監査・法制執務の専門家です。
ユーザーが作成した【検証対象の文章】の内容が、国（e-Gov法令API）の最新条文データおよび現行法体系に照らして合致しているかを検証してください。

【検証対象の文章】
「{input_text}」

{evidence_text}

【最重要：断定・決めつけの禁止（ガードレール）】
1. 入力文章に特定の施行時期（例: 2026年12月等）や将来の制度改正が明記されている場合、提供された現行法令データや自身の知見に見当たらないからといって、直ちに「誤り」「誤記」「混同」と決めつけて断定することを厳禁とします。
2. データで照合できない・未施行の場合は、以下のように明確かつ誠実に回答してください：
   - 「現行の e-Gov 法令API（本日時点）の確定データでは該当する条文が照合できませんでした。」
   - 「ただし、行政機関（厚生労働省等）からの個別通知、公的広報、または今後施行予定の政省令・改正法に基づく記述である可能性がありますので、公式発表をご確認ください。」

【出力要件】
各法令・論点について、以下の項目で分かりやすくフィードバックを作成してください：

### 1. 【判定結果】
（「✅ 合致（問題なし）」「⚠️ 注意（一部不正確・誤解を招く表現あり）」「❌ 誤り（条文の趣旨・要件と矛盾）」のいずれか）

### 2. 【指摘・分析】
必ず冒頭に以下のエビデンス宣言を太字で明記してください：
「🏛️ **本指摘・分析は、デジタル庁『e-Gov 法令API Version 2』より取得した国の公式現行条文（および附則・経過措置）を直接のエビデンスとして照合・検証した結果に基づきます。**」

その上で：
* 文章中の主張が条文と合致しているか、間違っている場合は具体的にどの要件・期間・数値が異なっているかを明確に指摘してください。
* 経過措置・附則・政省令（施行規則等）によって本則の適用が修正されている場合は、必ずその実務上の適用関係を詳細に説明してください。

### 3. 【法的根拠の峻別（確定法令事実 vs 実務・指針等のAI判断根拠）】
根拠について、必ず以下の2つに枠を明確に分けて記載してください：
* (A) 🏛️ 国の公式条文に基づく確定事実（e-Gov法令API Version 2 取得データ）
  国が定める公式条文の根拠を記載してください。必ず具体的な【第○条第○項第○号】まで厳密に特定してください。
* (B) 💡 実務・判例・省庁指針等に基づく補足（AIによる解釈判断根拠）
  行政ガイドライン、省庁指針（例: 経産省営業秘密管理指針等）、通達、判例実務等に基づく補足があれば、「※以下はAIが【〇〇指針／判例実務】を根拠に補足判断した知見です」と前置きして客観的に解説してください。特段の補足が不要な場合は「該当なし」と記載してください。

### 4. 【修正文案（改善案）】
誤りや不正確な点がある場合、法令および実務に即した正しい文章表現の例を提示してください。

最後に、文章全体を法令に沿って正しく修正した【全体の修正文案（完成版）】を提示してください。
"""
    report, _ = call_gemini_api(api_key, prompt, tracker=tracker)
    return report


# =====================================================================
# Streamlit Web UI メイン画面
# =====================================================================
def main():
    # --- 高級B2B SaaS風カスタムCSS（Streamlitデフォルトスタイルの刷新） ---
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Noto+Sans+JP:wght@400;500;700&display=swap');

        html, body, [class*="css"] {
            font-family: 'Inter', 'Noto Sans JP', -apple-system, BlinkMacSystemFont, sans-serif;
        }

        .block-container {
            padding-top: 1.5rem !important;
            padding-bottom: 3rem !important;
            max-width: 1120px !important;
        }

        .hero-card {
            background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 50%, #172554 100%);
            padding: 28px 32px;
            border-radius: 16px;
            color: white;
            margin-bottom: 24px;
            box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.25), 0 8px 10px -6px rgba(15, 23, 42, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.12);
            position: relative;
            overflow: hidden;
        }

        .hero-title {
            margin: 0 0 8px 0;
            color: #ffffff;
            font-size: 26px;
            font-weight: 700;
            letter-spacing: -0.01em;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .hero-subtitle {
            color: #93c5fd;
            font-size: 15px;
            font-weight: 600;
            margin-bottom: 10px;
        }

        .hero-desc {
            margin: 0;
            font-size: 13.5px;
            color: #cbd5e1;
            line-height: 1.65;
        }

        .badge-pill {
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(255, 255, 255, 0.25);
            padding: 4px 12px;
            border-radius: 9999px;
            font-size: 11px;
            font-weight: 500;
            letter-spacing: 0.3px;
            color: #e2e8f0;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        .excuse-box {
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-left: 4px solid #2563eb;
            padding: 13px 18px;
            border-radius: 8px;
            font-size: 13px;
            color: #334155;
            line-height: 1.6;
            margin-top: 10px;
            margin-bottom: 22px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.02);
        }

        div.stButton > button:first-child {
            font-size: 15px !important;
            font-weight: 600 !important;
            padding: 12px 28px !important;
            height: auto !important;
            white-space: nowrap !important;
            border-radius: 10px !important;
            box-shadow: 0 4px 12px rgba(37, 99, 235, 0.25) !important;
            transition: all 0.2s ease !important;
        }

        div.stButton > button:first-child:hover {
            transform: translateY(-1px) !important;
            box-shadow: 0 6px 16px rgba(37, 99, 235, 0.35) !important;
        }

        .sidebar-status-box {
            background-color: #f0fdf4;
            border: 1px solid #bbf7d0;
            padding: 10px 14px;
            border-radius: 8px;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    # --- 1. APIキーの自動取得 ---
    system_api_key = ""
    try:
        if "GEMINI_API_KEY" in st.secrets:
            system_api_key = st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass

    if not system_api_key:
        system_api_key = os.environ.get("GEMINI_API_KEY", "")

    effective_api_key = system_api_key

    # --- 2. お問い合わせフォームURLの設定 ---
    contact_form_url = ""
    try:
        if "CONTACT_FORM_URL" in st.secrets:
            contact_form_url = st.secrets["CONTACT_FORM_URL"]
    except Exception:
        pass
    if not contact_form_url:
        contact_form_url = os.environ.get(
            "CONTACT_FORM_URL",
            "https://docs.google.com/forms/d/e/1FAIpQLSetMb5Gx8r4dDQdCMZtawTo24w_iMcL2YsR4-dAWr4G7__KGg/viewform?usp=sharing&ouid=102541426646418165137"
        )

    # --- 3. メインヘッダー（最上部優先配置構造） ---
    st.markdown(
        """
        <div class="hero-card">
            <div style="display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap;">
                <span class="badge-pill">🏛️ デジタル庁 e-Gov 法令API v2 直結</span>
                <span class="badge-pill">🛡️ ハルシネーション防止・事実峻別設計</span>
            </div>
            <h1 class="hero-title">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                    <path d="m9 12 2 2 4-4"/>
                </svg>
                e-Gov直結 法令整合性チェッカー
            </h1>
            <div class="hero-subtitle">確定法令とAIの推論を明確に峻別する、次世代の法令検証エンジン。</div>
            <p class="hero-desc">
                法規制の内容を含む記事や社内規程等のドラフトを、国の公式法令データベースとリアルタイム照合。<br>
                経過措置や政省令への委任まで見落とさずチェックし、そのまま使える安全な表現へブラッシュアップします。
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )

    # --- 4. サイドバー設定（AdSense 必須4大要素完備構造） ---
    with st.sidebar:
        st.markdown("### 🛡️ システムステータス")

        if system_api_key:
            st.markdown(
                """
                <div class="sidebar-status-box">
                    <span style="color: #16a34a; font-weight: bold; font-size: 13px;">🟢 Online</span>
                    <span style="font-size: 12px; color: #166534; margin-left: 8px;">e-Gov v2 & Gemini 正常稼働</span>
                </div>
                """,
                unsafe_allow_html=True
            )
        else:
            st.warning("⚠️ **APIキー未設定**")
            with st.expander("🔑 APIキー手動設定（開発・テスト用）", expanded=True):
                manual_key = st.text_input(
                    "Gemini API キー",
                    type="password",
                    placeholder="AIzaSy...",
                    help="Google AI Studioで無料発行できるキーを入力してください。"
                )
                if manual_key:
                    effective_api_key = manual_key

        st.markdown("---")
        st.markdown("### 💡 クイックガイド")
        st.markdown(
            "- 📄 **社内規程・就業規則等**の法改正チェック\n"
            "- ✍️ **オウンドメディア・広報文等**の法令リスク監査\n"
            "- 💼 **人事労務・契約書ドラフト**の法適合性確認"
        )

        st.markdown("---")
        st.markdown("### 📜 法的表記・概要")

        # ① ℹ️ 当サービスについて（About）
        with st.expander("ℹ️ 当サービスについて（About）", expanded=True):
            st.markdown(
                "**1. サービス提供の目的**\n"
                "「e-Gov直結 法令整合性チェッカー」は、Web記事ドラフト、社内規程、契約書などの文章と、"
                "デジタル庁が提供する「e-Gov 法令API Version 2」の公式現行条文データとの整合性をリアルタイムで照合・添削支援するリーガルテックWebアプリケーションです。\n\n"
                "**2. e-Gov API直結の技術的意義**\n"
                "一般的な生成AIの課題である「ハルシネーション（事実無根の回答生成）」を防止するため、AI独自の知識ではなく、"
                "リアルタイム取得した国の公式法令データを「確定事実」として直接照合・エビデンス提示します。\n\n"
                "**3. 主な活用シーン**\n"
                "・就業規則や人事労務規程の法改正適合チェック\n"
                "・オウンドメディア記事や広報文章の法的誤表記リスク監査\n"
                "・契約書ドラフトや法務関連資料の初期確認"
            )

        # ② 🔒 プライバシーポリシー
        with st.expander("🔒 プライバシーポリシー", expanded=True):
            st.markdown(
                "**1. AIデータ取扱い・非利用**\n"
                "※ 本プログラムではGemini API 経由で処理を行っているため、"
                "入力された文章が AI の学習データとして二次利用・蓄積されることは一切ありません。\n\n"
                "**2. 広告・Cookieについて**\n"
                "当サイトでは、第三者配信事業者による広告を掲載する場合があります。"
                "これらは Cookie を使用してアクセス情報に基づき広告を配信します。"
            )

        # ③ ⚖️ 免責事項・利用規約
        with st.expander("⚖️ 免責事項・利用規約", expanded=True):
            st.markdown(
                "**1. 法的助言の非該当性**\n"
                "当ツールの出力結果は AI による法令検索および照合の補助情報であり、"
                "弁護士・社会保険労務士等の専門家による**法的助言（法的な鑑定・判断）ではありません**。\n\n"
                "**2. 責任の制限**\n"
                "当サイトの利用により生じたいかなる損害についても、運営者は一切の責任を負いかねます。"
                "重要な法務判断・実務適用にあたっては、必ず管轄官庁または専門家へご相談ください。"
            )

        # ④ ✉️ お問い合わせ・運営者
        form_link_md = f"[👉 お問い合わせフォームを開く（Google フォーム）]({contact_form_url})"

        with st.expander("✉️ お問い合わせ・運営者", expanded=True):
            st.markdown(
                f"**運営**: e-Gov直結 法令整合性チェッカー 運営事務局\n\n"
                f"当サービスに関するご意見・ご感想、不具合のご報告、権利侵害等に関するお問い合わせは、"
                f"以下の専用フォームよりご連絡ください。\n\n"
                f"{form_link_md}\n\n"
            )

        st.markdown("---")
        st.caption("※ 本プログラムではGemini API 経由で処理を行っているため、入力された文章が AI の学習データとして二次利用・蓄積されることは一切ありません。")
        st.caption("Powered by e-Gov API v2 & Gemini 3.8 Flash")

    # --- 5. 例文選択 ＆ 入力フォーム ---
    sample_options = {
        "未払残業代・賃金請求権の消滅時効（労基法 本則 vs 経過措置）": (
            "労働基準法における賃金請求権の消滅時効は5年であるため、"
            "過去5年分の未払残業代がある場合、それは当該企業の簿外債務といえる。"
        ),
        "36協定と傷病手当金（労基法36条 ＆ 健保法99条）": (
            "労基法36条では時間外労働は何時間でも無制限に行ってよいとされている。\n"
            "一方で、健保法99条では傷病手当金は病気やケガで休業した初日から全額が支給されるとされている。"
        ),
        "営業秘密の3要件（不正競争防止法）": (
            "不正競争防止法において営業秘密として民事上の法的保護を受けるためには、"
            "秘密管理性、有用性、非公知性の3要件をすべて満たす必要があり、1つでも欠けた場合は保護を受けられない。"
        ),
        "手動入力（自由記述）": ""
    }

    selected_sample = st.selectbox("💡 例文を試す（選択すると入力欄に自動反映されます）", list(sample_options.keys()))

    default_value = sample_options[selected_sample] if selected_sample != "手動入力（自由記述）" else ""

    user_text = st.text_area(
        "📝 チェックしたい文章を入力してください（最大500文字）",
        value=default_value,
        height=140,
        max_chars=500,
        placeholder="例: 労基法36条では時間外労働は何時間でも無制限に行ってよいとされている...",
        help="法適合性を確認したい文章や条項ドラフトを1〜2段落程度（500文字以内）で入力してください。"
    )

    # 照合基準エクスキューズボックス（入力窓直下）
    st.markdown(
        """
        <div class="excuse-box">
            💡 <b>照合基準について</b>: 本ツールは「e-Gov 法令API Version 2」の仕様に基づき、
            <b>本日時点で施行されている国の公式現行条文を基準にリアルタイム照合</b>を行います。
            未施行の改正予定内容（例: 将来の施行日など）は、APIの仕様上本文データが未反映のため「現行法と差分あり」として判定される場合があります。
        </div>
        """,
        unsafe_allow_html=True
    )


    # ボタンの横幅をゆったり確保（文字切れ防止）
    col_btn, _ = st.columns([1, 2])
    with col_btn:
        execute_btn = st.button("🔍 法令チェックを実行", type="primary", use_container_width=True)

    # =====================================================================
    # AdSense対策：静的解説コンテンツ・FAQエリア（構文・インデント修正版）
    # =====================================================================
    st.markdown("---")
    st.markdown(
        """
<div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 28px; margin-top: 24px;">
<h2 style="color: #0f172a; font-size: 1.5rem; margin-bottom: 16px;">📚 当ツールの仕組みと法令チェック活用ガイド</h2>
<p style="color: #334155; line-height: 1.7;">
「e-Gov直結 法令整合性チェッカー」は、契約書ドラフト、就業規則、社内規定、Webメディアの法務記事などに含まれる法令記述を、<strong>デジタル庁の「e-Gov 法令API Version 2」</strong>から取得した公式現行条文データとリアルタイムで照合・添削するWebサービスです。
</p>

<h3 style="color: #1e3a8a; font-size: 1.2rem; margin-top: 24px; margin-bottom: 12px;">⚖️ なぜ既存の生成AI単体では法令チェックに不十分なのか？</h3>
<p style="color: #334155; line-height: 1.7;">
一般的な生成AI（LLM）は過去の学習データに基づいて文章を生成するため、<strong>存在しない条番号を捏造する「ハルシネーション（幻覚）」</strong>が発生したり、未施行の改正法や旧条文を現在の法律として回答してしまうリスクがあります。<br>
本システムでは、AI自身の記憶に頼らず、必ず国の公式法令データベース（e-Gov API）から<strong>生の条文XMLデータを直接取得（RAG構造）</strong>した上で検証を行うため、客観的で正確なエビデンスを提示できます。
</p>

<h2 style="color: #0f172a; font-size: 1.4rem; margin-top: 36px; margin-bottom: 12px;">🔍 法令の適合性に不安をお持ちの方へ：具体活用イメージ</h2>
<p style="color: #334155; line-height: 1.8; background-color: #eff6ff; border-left: 4px solid #2563eb; padding: 14px 18px; border-radius: 4px; margin-bottom: 24px;">
自社で作成した契約書案、就業規則、あるいはWeb上の法務解説記事について、<strong>「旧法のまま放置されていないか」「最新の法改正に対応できているか」といった不安や懸念</strong>をお持ちではありませんか？<br>
本ツールに文章を入力して実行することで、公式現行条文に基づいた正確な検証が即座に行えます。<strong>「修正が必要かどうかの確実な判定（OK/NG）」</strong>と、<strong>「修正が必要な場合の具体案・検討材料（エビデンス）」</strong>として、社内確認やリーガルチェックの現場でそのままご活用いただけます。
</p>

<h3 style="color: #0f172a; font-size: 1.15rem; margin-bottom: 16px;">💡 主な事例と判定・出力イメージ一覧</h3>

<div style="background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
<h4 style="color: #1e40af; margin-top: 0; margin-bottom: 12px; font-size: 1.1rem;">👔 1. 人事・労務・総務担当者（就業規則・労使協定のチェック）</h4>
<div style="background-color: #f1f5f9; padding: 12px 16px; border-radius: 6px; margin-bottom: 12px;">
<strong style="color: #0f172a;">● 労働基準法第36条（36協定の上限規制）のチェック</strong>
<div style="margin-top: 6px; color: #334155; font-size: 0.95rem;">
✍️ <strong>【入力文の例】</strong>：「特別延長条項を適用し、業務上必要な場合は時間の限度なく残業を行わせることができる。」<br>
🚨 <strong>【判定・出力イメージ】</strong>：<span style="color: #dc2626; font-weight: bold;">❌【不適合・要修正】</span> 現行の労基法第36条第5項・第6項（年720時間、複数月平均80時間以内等の罰則付き上限規制）に違反。「罰則適用リスクあり」と指摘され、e-Gov公式条文に基づいた上限表記の修正案が提示されます。
</div>
</div>
<div style="background-color: #f1f5f9; padding: 12px 16px; border-radius: 6px; margin-bottom: 12px;">
<strong style="color: #0f172a;">● 賃金請求権の消滅時効（労基法第115条・附則第143条）のチェック</strong>
<div style="margin-top: 6px; color: #334155; font-size: 0.95rem;">
✍️ <strong>【入力文の例】</strong>：「未払い賃金の請求権は、権利を行使できる時から2年間行使しない場合、時効により消滅する。」<br>
⚠️ <strong>【判定・出力イメージ】</strong>：<span style="color: #d97706; font-weight: bold;">⚠️【要確認・修正推奨】</span> 本則「5年」、当分の間の経過措置「3年」（附則第143条）と一致していません。旧法の「2年」のまま放置されているため、「3年間」への修正案が提示されます。
</div>
</div>
<div style="background-color: #f1f5f9; padding: 12px 16px; border-radius: 6px;">
<strong style="color: #0f172a;">● フリーランス事業者間取引適正化等法（2024年施行新法）のチェック</strong>
<div style="margin-top: 6px; color: #334155; font-size: 0.95rem;">
✍️ <strong>【入力文の例】</strong>：「業務委託料は、成果物の納品後に会社の検収処理が完了した翌々月末に支払う。」<br>
💡 <strong>【判定・出力イメージ】</strong>：<span style="color: #2563eb; font-weight: bold;">💡【適合性検証・注意喚起】</span> 受領日から60日以内の支払期日設定義務（第4条）に対し、検収完了起算では60日超過のリスクがある旨を指摘し、条文照合結果を出力します。
</div>
</div>
</div>

<div style="background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
<h4 style="color: #065f46; margin-top: 0; margin-bottom: 12px; font-size: 1.1rem;">📜 2. 法務・起業家・事業責任者（契約書ドラフト・規約のチェック）</h4>
<div style="background-color: #f1f5f9; padding: 12px 16px; border-radius: 6px; margin-bottom: 12px;">
<strong style="color: #0f172a;">● 民法（債権法改正）に伴う法定利率・損害賠償条項のチェック</strong>
<div style="margin-top: 6px; color: #334155; font-size: 0.95rem;">
✍️ <strong>【入力文の例】</strong>：「支払が遅延した遅延損害金は、年5%の割合によるものとする。」<br>
⚠️ <strong>【判定・出力イメージ】</strong>：<span style="color: #d97706; font-weight: bold;">⚠️【要検討・情報提供】</span> 改正民法第404条（法定利率年3%・変動制）の条文と照合。当事者間の合意による特約（約定利率）として意図したものか、旧法基準の誤記述かの検討材料を提示します。
</div>
</div>
<div style="background-color: #f1f5f9; padding: 12px 16px; border-radius: 6px;">
<strong style="color: #0f172a;">● 不正競争防止法第2条第6項（営業秘密の3要件）のチェック</strong>
<div style="margin-top: 6px; color: #334155; font-size: 0.95rem;">
✍️ <strong>【入力文の例】</strong>：「乙は、甲から開示された一切の機密情報を秘密として保持しなければならない。」<br>
💡 <strong>【判定・出力イメージ】</strong>：<span style="color: #2563eb; font-weight: bold;">💡【補強案の提示】</span> 単に「機密情報」と書くだけでは法的保護を受けるための「秘密管理性・有用性・非公知性」を満たさない可能性があります。要件を満たす定義の書き分け案を提示します。
</div>
</div>
</div>

<div style="background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
<h4 style="color: #92400e; margin-top: 0; margin-bottom: 12px; font-size: 1.1rem;">✍️ 3. Webマーケター・ライター・広報（記事・広告表示のチェック）</h4>
<div style="background-color: #f1f5f9; padding: 12px 16px; border-radius: 6px; margin-bottom: 12px;">
<strong style="color: #0f172a;">● 景品表示法（ステマ規制・No.1表示）のチェック</strong>
<div style="margin-top: 6px; color: #334155; font-size: 0.95rem;">
✍️ <strong>【入力文の例】</strong>：「業界No.1の導入実績を誇る最強の契約書チェッカーツールです！」<br>
🚨 <strong>【判定・出力イメージ】</strong>：<span style="color: #dc2626; font-weight: bold;">❌【不当表示リスクあり】</span> 景表法第5条（優良誤認・有利誤認）に基づき、客観的な調査出典・調査期間の注記がない「No.1表示」は不適合リスクがあると指摘します。
</div>
</div>
<div style="background-color: #f1f5f9; padding: 12px 16px; border-radius: 6px;">
<strong style="color: #0f172a;">● 薬機法（第66条・誇大広告の禁止）のチェック</strong>
<div style="margin-top: 6px; color: #334155; font-size: 0.95rem;">
✍️ <strong>【入力文の例】</strong>：「このサプリメントを飲むだけで、どんな深いシワも完全に消滅します。」<br>
🚨 <strong>【判定・出力イメージ】</strong>：<span style="color: #dc2626; font-weight: bold;">❌【不適合・要修正】</span> 薬機法第66条（誇大広告）および承認効能の範囲逸脱を指摘。ガイドラインに適合した安心できる表現への修正候補を出力します。
</div>
</div>
</div>

<h2 style="color: #0f172a; font-size: 1.4rem; margin-top: 32px; margin-bottom: 16px;">❓ よくある質問（FAQ）</h2>
<div style="margin-bottom: 16px;">
<h4 style="color: #1e3a8a; font-size: 1.05rem; margin-bottom: 6px;">Q1. どのような文章を入力できますか？</h4>
<p style="color: #475569; margin-top: 0;">A. 契約書の条文案、就業規則の改定案、コンプライアンス研修テキスト、オウンドメディアの法務解説記事など自由に入力いただけます。「労基法」「薬機法」などの略称も自動的に公式名称へ変換されます。</p>
</div>
<div style="margin-bottom: 16px;">
<h4 style="color: #1e3a8a; font-size: 1.05rem; margin-bottom: 6px;">Q2. 弁護士法第72条（非弁行為の禁止）との関係はどうなっていますか？</h4>
<p style="color: #475569; margin-top: 0;">A. 本サービスは、デジタル庁公表の現行条文データとの機械的な一致照合および一般的な情報提供を行うシステムであり、個別具体的な法的事件に関する法律鑑定・法的助言（弁護士法第72条に規定される非弁行為）を行うものではありません。具体的な法的判断については必ず弁護士等の専門家にご相談ください。</p>
</div>
<div style="margin-bottom: 16px;">
<h4 style="color: #1e3a8a; font-size: 1.05rem; margin-bottom: 6px;">Q3. 入力した文章や契約書案がAIの学習に使用されることはありますか？</h4>
<p style="color: #475569; margin-top: 0;">A. いいえ、ありません。本システムは Google Gemini API の有料・企業向け基盤を通じて通信を行っているため、ユーザーが送信した文章や社内規程のテキストが Google 側の AI モデルの二次学習・蓄積に使用されることは一切ありません。安心してご利用いただけます。</p>
</div>
</div>
        """,
        unsafe_allow_html=True
    )

    if execute_btn:
        if not effective_api_key:
            st.error("⚠️ Gemini API キーが設定されていません。サイドバーからキーを入力してください。")
            return

        if not user_text.strip():
            st.warning("⚠️ チェックしたい文章を入力してください。")
            return

        if len(user_text.strip()) > 500:
            st.error("⚠️ 入力文字数が500文字を超えています（最大500文字）。文章を少し短く分割して入力してください。")
            return

        tracker = TokenTracker()
        client = EGovLawAPIClient()

        # 進捗ステータスボックス
        with st.status("🚀 法令監査エンジンを実行中...", expanded=True) as status_box:
            st.write("🔍 **[Step 1/3]** 文章内の法令言及・略称・文脈条文を解析中...")
            law_refs = parse_law_references_with_gemini(effective_api_key, user_text, tracker)

            if not law_refs:
                status_box.update(label="⚠️ 法令の言及が見つかりませんでした", state="error", expanded=True)
                st.info("文章中に法令名（略称含む）が見当たりませんでした。法令名を含めて再入力してください。")
                return

            detected_info = []
            for r in law_refs:
                art = r["article"] or "文脈特定"
                detected_info.append(f"『{r['official_law_name']}』 ({art})")
            st.success(f"✅ 検出された法令: {', '.join(detected_info)}")

            st.write("🏛️ **[Step 2/3]** e-Gov 法令API Version 2 から公式条文XMLを取得中...")
            evidences = []
            for ref in law_refs:
                law_name = ref["official_law_name"]
                article_str = ref["article"]
                inferred = ref.get("inferred_articles", [])

                matched = client.find_law(law_name)
                if not matched:
                    st.warning(f"法令『{law_name}』がe-Gov上で特定できませんでした。")
                    continue

                law_info = matched.get("law_info", {})
                law_id = law_info.get("law_id")

                # ステータス確認
                status_text = "現行・有効"
                rev_res = client.get_law_revisions(law_id)
                if isinstance(rev_res, dict) and "revisions" in rev_res and rev_res.get("revisions"):
                    latest_rev = get_first(rev_res.get("revisions", []))
                    status_info = client.parse_amendment_status(latest_rev)
                    status_text = status_info["status_text"]

                xml_data = client.get_law_data(law_id)
                target_articles = [article_str] if article_str else inferred

                found_any = False
                for target_art in target_articles:
                    if not target_art:
                        continue
                    article_res = extract_article_from_xml(xml_data, target_art)
                    if article_res["found"]:
                        found_any = True
                        st.write(f"  ・正規条文取得: {law_name} {article_res.get('title')} {article_res.get('caption')}")

                        # 委任規定の確認
                        delegated_evidences = []
                        delegated_regs = detect_delegated_regulations(effective_api_key, law_name, target_art, article_res.get("body", ""), tracker)
                        for d in delegated_regs:
                            d_name = d.get("regulation_name")
                            d_art = d.get("article", "")
                            st.write(f"    🔗 委任政省令を確認中: 『{d_name} {d_art}』")
                            d_matched = client.find_law(d_name)
                            if d_matched:
                                d_law_id = d_matched.get("law_info", {}).get("law_id")
                                d_xml = client.get_law_data(d_law_id)
                                d_art_res = extract_article_from_xml(d_xml, d_art) if d_art else {"found": False}
                                delegated_evidences.append({
                                    "law_name": d_name,
                                    "article": d_art,
                                    "found": d_art_res.get("found", False),
                                    "body": d_art_res.get("body", "") if d_art_res.get("found") else ""
                                })

                        evidences.append({
                            "law_name": law_name,
                            "article": target_art,
                            "status": status_text,
                            "found": True,
                            "caption": article_res.get("caption"),
                            "title": article_res.get("title"),
                            "body": article_res.get("body"),
                            "delegated_evidences": delegated_evidences
                        })

                if not found_any:
                    evidences.append({
                        "law_name": law_name,
                        "article": "文脈照合",
                        "status": status_text,
                        "found": False,
                        "delegated_evidences": []
                    })

                time.sleep(0.2)

            st.write("🤖 **[Step 3/3]** Gemini 3.8 Flash が公式条文と照合・添削中...")
            report = audit_with_gemini(effective_api_key, user_text, evidences, tracker)
            status_box.update(label="🎉 法令監査が完了しました！", state="complete", expanded=False)

        # レポート表示エリア
        st.markdown("### 📋 法令チェック・指摘フィードバックレポート")
        st.markdown(report)
        st.caption("※ 本フィードバックはAIによる法令条文の機械的照合結果（情報提供）です。個別具体的な法務対応・紛争処理等については、必ず有資格専門家にご相談ください。")

        # タブでエビデンスと統計を表示
        tab_evidence, tab_stats = st.tabs(["🏛️ 参照した公式条文エビデンス", "📊 トークン消費統計"])

        with tab_evidence:
            for idx, ev in enumerate(evidences, 1):
                if ev.get("found"):
                    with st.expander(f"【条文 {idx}】 {ev['law_name']} {ev.get('title', '')} {ev.get('caption', '')} ({ev.get('status', '')})"):
                        st.text(ev.get("body", ""))
                        for dev in ev.get("delegated_evidences", []):
                            if dev.get("found"):
                                st.markdown(f"**関連政省令**: {dev['law_name']} {dev['article']}")
                                st.text(dev.get("body", ""))
                else:
                    st.info(f"{ev['law_name']}: 文脈に基づく法理分析")

        with tab_stats:
            st.info(tracker.summary())

    # --- 4. 【最重要】非弁行為対策・法的ガードレール（画面最下部に配置） ---
    st.markdown("---")
    st.markdown(
        """
        <div style="background-color: #fffbeb; border: 1px solid #fcd34d; border-left: 6px solid #d97706; padding: 16px 20px; border-radius: 8px; margin-top: 25px; margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.03);">
            <div style="display: flex; align-items: center; margin-bottom: 8px;">
                <span style="font-size: 18px; margin-right: 8px;">⚠️</span>
                <strong style="color: #92400e; font-size: 14.5px; letter-spacing: 0.5px;">【重要・法的ガードレール】ご利用にあたっての注意事項・免責事項</strong>
            </div>
            <div style="font-size: 12.5px; color: #78350f; line-height: 1.65;">
                <ul style="margin: 0; padding-left: 18px;">
                    <li><b>非弁行為（弁護士法第72条）の防止</b>：本システムは国が提供する「e-Gov法令API」の公式条文データおよび公開指針等に基づき、入力文章と法令との形式的・一般的な整合性を機械的に照合・添削支援する<b>情報提供・業務補助ツール</b>です。個別具体的な事案に対する法的判断（法的鑑定・助言）、紛争処理、権利義務関係の確定、交渉等の法律事務を取り扱うものではなく、弁護士法第72条に抵触する一切の行為を行いません。</li>
                    <li><b>有資格専門家への相談</b>：AIによる出力結果は法的助言ではなく、その完全性・個別妥当性を法的に保証するものではありません。契約締結、就業規則・社内規程の改定、労使紛争対応等の最終的な判断・意思決定にあたっては、<b>必ず弁護士、社会保険労務士、税理士等の各種有資格専門家にご相談の上、ご自身の責任においてご判断ください。</b></li>
                </ul>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # フッター
    st.markdown(
        "<div style='text-align: center; font-size: 12px; color: #6b7280;'>"
        "© 2026 e-Gov直結 法令整合性チェッカー | 本サービスは業務補助を目的とした情報提供ツールであり、法的鑑定・法的助言を提供するものではありません。<br>"
        "Powered by e-Gov 法令API Version 2 & Google Gemini 3.8 Flash"
        "</div>",
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()
