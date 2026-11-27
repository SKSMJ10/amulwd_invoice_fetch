function sendTelegram(env, chatId, text) {
  return fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      parse_mode: "HTML"
    })
  });
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("Amul Bot Ready", { status: 200 });

    if (
      env.TELEGRAM_WEBHOOK_SECRET &&
      request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TELEGRAM_WEBHOOK_SECRET
    ) {
      return new Response("Forbidden", { status: 403 });
    }

    let chatId = null;
    try {
      const update = await request.json();
      const message = update.message;
      if (!message || !message.text || !message.chat) return new Response("OK", { status: 200 });

      // Return 200 on unauthorized chats so Telegram does not retry the webhook update
      const allowedChatId = String(env.TELEGRAM_CHAT_ID ?? "").trim();
      if (!allowedChatId || String(message.chat.id) !== allowedChatId) {
        return new Response("OK", { status: 200 });
      }

      chatId = message.chat.id;
      const text = message.text.trim().toLowerCase();

      let dateFilter = null;
      let label = "";

      if (text === "/t" || text === "t" || text === "/today") {
        dateFilter = "Today";
        label = "Today's invoice";
      } else if (text === "/y" || text === "y" || text === "/yesterday") {
        dateFilter = "Yesterday";
        label = "Yesterday's invoice";
      } else if (text === "/r" || text === "r" || text === "/report" || text === "/get") {
        dateFilter = "smart";
        label = "latest invoice report";
      }

      if (dateFilter) {
        const [, ghResponse] = await Promise.all([
          sendTelegram(env, chatId, `⏳ <i>Fetching ${label}... (~30-45s)</i>`).catch((err) =>
            console.error(err)
          ),
          fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/report.yml/dispatches`, {
            method: "POST",
            headers: {
              "Authorization": `Bearer ${env.GITHUB_PAT}`,
              "User-Agent": "AmulBot-Worker",
              "Accept": "application/vnd.github.v3+json",
              "Content-Type": "application/json"
            },
            body: JSON.stringify({
              ref: "master",
              inputs: {
                date_filter: dateFilter
              }
            })
          })
        ]);

        if (!ghResponse.ok) {
          const errText = await ghResponse.text();
          await sendTelegram(
            env,
            chatId,
            `❌ <b>Failed to trigger GitHub Action:</b>\n<code>${errText}</code>`
          ).catch((err) => console.error(err));
        }
      }
    } catch (err) {
      console.error(err);
      if (env.TELEGRAM_BOT_TOKEN && chatId) {
        await sendTelegram(
          env,
          chatId,
          `❌ <b>Worker Error:</b>\n<code>${err.message}</code>`
        ).catch((notifyErr) => console.error(notifyErr));
      }
    }
    return new Response("OK", { status: 200 });
  }
};
