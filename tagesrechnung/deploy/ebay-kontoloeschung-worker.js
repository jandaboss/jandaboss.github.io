// Empfangsstelle für eBays „Marketplace Account Deletion“-Meldungen (Cloudflare Worker, kostenlos).
//
// eBay verlangt von jedem Programm mit Zugang zur eBay-Schnittstelle, dass es Meldungen über
// gelöschte eBay-Konten empfängt. Dieser Worker beantwortet eBays Prüfanfrage und bestätigt jede
// Meldung. Rechnungen werden dabei nicht gelöscht: Sie unterliegen der gesetzlichen
// Aufbewahrungspflicht (§ 147 AO, § 14b UStG). Weitere Daten zu eBay-Käufern speichert das
// Rechnungsprogramm nicht.
//
// Einrichtung: README.md, Abschnitt „eBay-Meldungen über gelöschte Konten“.
// Bei eBay als Endpoint die Worker-Adresse MIT „/ebay“ am Ende eintragen, z. B.
//   https://mein-shop-ebay.meinname.workers.dev/ebay

// Derselbe Token wie bei eBay unter „Verification token“: 32–80 Zeichen, nur A–Z, a–z, 0–9, _ und -
const VERIFICATION_TOKEN = "HIER_DEN_TOKEN_EINTRAGEN";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    // Muss exakt der bei eBay eingetragenen Adresse entsprechen (ohne ?challenge_code=…)
    const endpoint = url.origin + url.pathname;

    if (request.method === "GET") {
      const challengeCode = url.searchParams.get("challenge_code");
      if (!challengeCode) {
        return new Response("eBay-Empfangsstelle ist bereit.", { status: 200 });
      }
      // eBay prüft: SHA-256 (hex) von challengeCode + verificationToken + endpoint
      const daten = new TextEncoder().encode(challengeCode + VERIFICATION_TOKEN + endpoint);
      const hash = await crypto.subtle.digest("SHA-256", daten);
      const challengeResponse = [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
      return new Response(JSON.stringify({ challengeResponse }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    if (request.method === "POST") {
      // Meldung über ein gelöschtes eBay-Konto: bestätigen (eBay erwartet 200, 201, 202 oder 204).
      return new Response(null, { status: 204 });
    }

    return new Response("Methode nicht erlaubt", { status: 405 });
  },
};
