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
      // 3. クローラー対策（コンパクト＆スタイリッシュなフッター）
      .on("body", {
        element(element) {
          element.append(`
<div id="fallback-seo-content" style="max-width: 900px; margin: 24px auto 12px; padding: 12px 16px; background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 11px; line-height: 1.5; color: #64748b;">
  <div style="display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; margin-bottom: 8px;">
    <span style="font-weight: 700; font-size: 12px; color: #1e293b;">🏛️ e-Gov直結 法令整合性チェッカー</span>
    <span style="font-size: 10px; color: #94a3b8;">デジタル庁 e-Gov 法令API v2 直結</span>
  </div>
  <p style="margin: 0 0 6px 0; color: #475569;">
    確定法令とAIの推論を明確に峻別する次世代の法令検証エンジン。契約書・就業規則・オウンドメディア記事等の法令適合性をリアルタイムで照合・監査します。
  </p>
  <div style="display: flex; gap: 12px; margin-bottom: 6px; font-size: 10px; color: #475569; font-weight: 500;">
    <span>✓ ハルシネーション排除</span>
    <span>✓ AI非学習宣言</span>
    <span>✓ 弁護士法第72条準拠</span>
  </div>
  <p style="margin: 0; font-size: 10px; color: #94a3b8; border-top: 1px dashed #cbd5e1; padding-top: 6px;">
    ※本ツールは機械的な照合・情報提供を目的とする業務補助ツールです。法的鑑定・法的助言を提供するものではありません。 ｜ 運営: e-Gov直結 法令整合性チェッカー 運営事務局
  </p>
</div>
          `, { html: true });
        }
      })
      .transform(response);
  }
};
