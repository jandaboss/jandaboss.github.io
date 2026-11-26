// Klick auf das Symbol in der Symbolleiste öffnet den Konverter in einem neuen Tab.
const CONVERTER_URL = chrome.runtime.getURL("converter.html");

chrome.action.onClicked.addListener(() => {
  chrome.tabs.create({ url: CONVERTER_URL });
});

// Nach der ersten Installation einmal öffnen, damit man sieht, wo es losgeht.
chrome.runtime.onInstalled.addListener((details) => {
  if (details.reason === "install") {
    chrome.tabs.create({ url: CONVERTER_URL + "#willkommen" });
  }
});
