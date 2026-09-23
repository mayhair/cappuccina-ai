import { speechText } from "../src/lib/speech.js";

const msg = (text) => ({ parts: [{ type: "text", text }] });

const cases = [
  ["Yay! 🎉", "[excited] Yay!"],
  ["I'm sad. 😭 What did I do?", "[sad] I'm sad. What did I do?"],
  ["I'm 😄 glad you liked it.", "[happy] I'm glad you liked it."],
  ["😄 I'm glad.", "[happy] I'm glad."],
  ["The angle is 45° today ☕", "The angle is 45° today"],
  ["Fresh out of the machine! ☕🎭🩰", "Fresh out of the machine!"],
  ["Okay, busted. 🎭 Yes, that's me.", "Okay, busted. Yes, that's me."],
  ["I came from internet silliness ✨ and now I help you study 🔥.", "[delighted, excited] I came from internet silliness and now I help you study."],
  ["Wow 😮😯!", "[surprised] Wow!"],
  ["So happy ❤️❤️ for you 🥳", "[warm, excited] So happy for you"],
  ["🎵", ""],
  ["🎵🎵🎵", ""],
  ["Really? 🫠😅", "[nervous] Really?"],
  ["**Bold** and `code` and [link](http://x) 🙂", "[happy] Bold and code and link"],
  ["Line one 🎉\nLine two 😭", "[excited] Line one [sad] Line two"],
];

let failures = 0;
for (const [input, expected] of cases) {
  const actual = speechText(msg(input));
  const ok = actual === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${JSON.stringify(input)}`);
  if (!ok) {
    console.log(`        expected ${JSON.stringify(expected)}`);
    console.log(`        actual   ${JSON.stringify(actual)}`);
  }
}
console.log(failures ? `\n${failures} failure(s)` : "\nall cases passed");
process.exit(failures ? 1 : 0);
