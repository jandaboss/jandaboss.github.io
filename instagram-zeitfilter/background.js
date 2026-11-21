// Klick auf das Symbol in der Chrome-Leiste: Fenster auf Instagram öffnen
chrome.action.onClicked.addListener(async (tab) => {
  if (tab.url && tab.url.startsWith("https://www.instagram.com/")) {
    try {
      await chrome.tabs.sendMessage(tab.id, "toggle-zeitfilter");
    } catch (_) {
      // Content-Script noch nicht geladen (z. B. Tab war schon vor der Installation offen)
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
      await chrome.tabs.sendMessage(tab.id, "toggle-zeitfilter");
    }
  } else {
    chrome.tabs.create({ url: "https://www.instagram.com/" });
  }
});
