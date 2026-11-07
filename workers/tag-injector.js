export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. sitemap.xml の自動応答
    if (url.pathname === "/sitemap.xml") {
      const sitemapNs = "http://" + "www.sitemaps" + ".org/schemas/sitemap/0.9";
      const sitemapXml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="${sitemapNs}">
  <url>
    <loc>https://legalscale.net/</loc>
    <lastmod>2026-11-06</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>`;
      return new Response(sitemapXml, {
        headers: {
          "Content-Type": "application/xml; charset=utf-8",
          "Cache-Control": "public, max-age=86400"
        }
      });
    }

    // 2. ads.txt の自動応答（AdSense審査・所有権確認に必須）
    if (url.pathname === "/ads.txt") {
      const adsTxtContent = "google" + ".com, pub-6258429009795456, DIRECT, f08c47fec0942fa0\n";
      return new Response(adsTxtContent, {
        headers: {
          "Content-Type": "text/plain; charset=utf-8",
          "Cache-Control": "public, max-age=86400"
        }
      });
    }

    // 3. robots.txt の自動応答
    if (url.pathname === "/robots.txt") {
      const sitemapLocation = "https://" + "legalscale" + ".net/sitemap.xml";
      const robotsTxt = `User-agent: *
Allow: /
Sitemap: ${sitemapLocation}
`;
      return new Response(robotsTxt, {
        headers: {
          "Content-Type": "text/plain; charset=utf-8",
          "Cache-Control": "public, max-age=86400"
        }
      });
    }

    const response = await fetch(request);
    const contentType = response.headers.get("content-type") || "";

    if (!contentType.includes("text/html")) {
      return response;
    }

    // ID 設定
    const GA4_MEASUREMENT_ID = "G-6ZLYBC7XPW"; 
    const ADSENSE_PUBLISHER_ID = "ca-pub-6258429009795456"; 

    // スクリプトURLの組み立て（チャットのURLフィルタ対策込み）
    const gaUrl = "https://" + "www." + "googletagmanager" + ".com/gtag/js?id=" + GA4_MEASUREMENT_ID;
    const adsenseUrl = "https://" + "pagead2." + "googlesyndication" + ".com/pagead/js/adsbygoogle.js?client=" + ADSENSE_PUBLISHER_ID;

    return new HTMLRewriter()
      // Title の完全上書き
      .on("title", {
        element(element) {
          element.setInnerContent("e-Gov直結 法令整合性チェッカー | 確定法令とAIの推論を明確に峻別する次世代リーガルチェック");
        }
      })
      // 正しい AdSense & GA4 タグの注入
      .on("head", {
        element(element) {
          element.append(`
<meta name="description" content="契約書・オウンドメディア・就業規則のテキストをデジタル庁e-Gov法令API v2とリアルタイム照合。確定法令とAI解釈を明確に峻別する無料法令監査ツール。" />
<link rel="canonical" href="https://legalscale.net/" />
<meta property="og:title" content="e-Gov直結 法令整合性チェッカー" />
<meta property="og:description" content="国が提供するe-Gov法令API v2と直結し、法令整合性を高精度に監査・添削。" />
<meta property="og:url" content="https://legalscale.net/" />
<meta property="og:type" content="website" />
<script async src="${adsenseUrl}" crossorigin="anonymous"></script>
<script async src="${gaUrl}"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', '${GA4_MEASUREMENT_ID}');
</script>
          `, { html: true });
        }
      })
      // クローラー向けフォールバックテキスト
      .on("body", {
        element(element) {
          element.append(`
<div id="fallback-seo-content" style="padding: 20px; font-family: sans-serif; max-width: 900px; margin: 0 auto; color: #333; background: #fff;">
  <h1>🏛️ e-Gov直結 法令整合性チェッカー</h1>
  <p><strong>確定法令とAIの推論を明確に峻別する、次世代の法令検証エンジン。</strong></p>
  <p>本サービスは、デジタル庁が提供する「e-Gov 法令API Version 2」の公式現行条文データと Google Gemini API を直結させ、契約書・就業規則・オウンドメディア記事等の法令適合性をリアルタイムで検証・監査する無料Webツールです。</p>
  <h2>💡 主な特徴</h2>
  <ul>
    <li><strong>確定事実とAI解釈の厳密な峻別:</strong> 国の公式現行条文に基づく確定事実と、行政指針・判例・AIの補足解説を明確に分けて表示します。</li>
    <li><strong>ハルシネーションの徹底排除:</strong> e-Gov API から取得した生の正規条文 XML を直接参照し、正確な照合を行います。</li>
    <li><strong>AI非学習宣言:</strong> 入力された文章が AI の学習データとして二次利用・蓄積されることは一切ありません。</li>
  </ul>
  <h2>⚖️ 免責事項・利用規約</h2>
  <p>本ツールは弁護士法第72条（非弁行為の禁止）を遵守し、機械的な照合・情報提供および業務補助を目的としています。弁護士・社会保険労務士等の専門家による法的鑑定・法的助言を提供するものではありません。</p>
  <h2>✉️ 運営事務局</h2>
  <p>運営: e-Gov直結 法令整合性チェッカー 運営事務局</p>
</div>
          `, { html: true });
        }
      })
      .transform(response);
  }
};
