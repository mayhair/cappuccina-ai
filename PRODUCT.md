# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React + Vite (user-selected from options; UI mockup first, real backend later)

## Users

Primary users: secondary schoolers — tweens and early teens (~11–15). They use it in casual at-home or study situations: open-ended chat about anything (questions, boredom, feelings, curiosity), and on-demand tutoring. They are already fluent with modern chat interfaces (ChatGPT-like) and expect that grammar; they respond to a friendly, characterful presence rather than a sterile tool.

## Product Purpose

Ballerina Cappuccina is a general-purpose AI chatbot with a specific friendly personality, embodied by the character Ballerina Cappuccina (a cup-headed ballerina in a pink tutu). Open-ended conversation is the default mode. When the user is motivated, she acts as an effective tutor for any subject the user names — tutoring is still a chat session, but with a tutor system prompt so she teaches rather than just answers.

## Positioning

A character-embodied companion-tutor: unlike generic chatbots, the personality and visual identity (Ballerina Cappuccina) make talking to an AI feel warm and approachable for young users, while the app stays clearly identifiable as an AI chatbot.

## Operating Context

Laptop web browser. Sessions are casual and conversational; a user may switch between hanging out and getting homework help within the same day (tutoring enters via user intent and runs as a distinct chat mode).

## Capabilities and Constraints

- Open-ended chat about anything (default mode)
- Tutoring mode for any named subject: chat session with a tutor system prompt; entered by user intent/motivation
- Chat grammar familiar from modern AI chatbots (bubbles, input box, streaming responses); not radically dumbed down for age
- Visual flair unique to this app layered on top of the familiar grammar
- Light and dark mode support (user-confirmed requirement; dark mode = nighttime/stormy sky mood)
- Optional Adult mode: an 18+ user turns it on from Settings by entering a birthday (checked in the browser, never sent or stored). The account stores one boolean, so it follows the user across devices; when on, the chat prompt drops the young-user safety block and treats adult topics plainly, staying honest and emergency-aware. Operators can remove the option entirely with the `ADULT_MODE_ENABLED` env var
- The app must remain clearly identifiable as an AI chatbot (visible AI disclosure; the default audience is minors, with Adult mode as an explicit 18+ opt-in)
- This phase: UI mockup/prototype only — frontend before backend

## Brand Commitments

- Character: Ballerina Cappuccina (cup-headed ballerina, pink tutu, big amber eyes) — the reference image the user provided is the canonical look
- Friendly, approachable personality tuned for tweens/early teens
- Warm, reassuring copy voice (fused from the café direction at user request): lines like "I'm an AI ❤", "She's not a person — but she loves to help", "always double-check important stuff"
- Friendly rounded display type for headings (user-confirmed preference from the café direction)
- Mockup placeholder art is original generated art inspired by (not copying) the character; user supplies final character assets later

## Evidence on Hand

- One reference image of Ballerina Cappuccina (user-provided) — visual anchor for placeholder art
- No real content, testimonials, or data; all demonstration content in the mockup is synthetic and labeled for later replacement

## Product Principles

1. Familiar first: the chat grammar kids already know, no re-learning
2. Presence, not costume: the character is felt in the experience, not pasted on
3. Honest about being an AI, warm while doing it
4. Tutoring is a mode, not a different product: motivation is invited, never forced
5. Age-clear language: simple, kind, never condescending

## Accessibility & Inclusion

Audience includes younger readers: clear type sizes, plain language, obvious affordances and states. Standard web accessibility baseline (contrast, focus visibility, keyboard use).
