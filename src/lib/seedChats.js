// Seed conversations mirroring the approved comp; the runtime appends from here.
export const initialChats = [
  {
    id: 'hello',
    title: 'Cappuccina chat',
    tag: null,
    messages: [
      {
        role: 'assistant',
        content:
          "Ciao! I'm Cappuccina — an AI who loves big questions. What's on your mind today?",
      },
      { role: 'user', content: "I have a science test tomorrow and I'm kinda nervous" },
      { role: 'assistant', content: "You've got this! 💕 I'm here for you — want to quiz through it together?" },
    ],
  },
  {
    id: 'fractions',
    title: 'Fractions tutoring',
    tag: 'study',
    messages: [
      {
        role: 'assistant',
        content:
          "Ciao! Ready to become a fractions star? We'll go slow — one question at a time. 💕",
      },
      { role: 'user', content: 'okay… start with something easy' },
      { role: 'assistant', content: 'If I eat 1 slice of a pizza cut into 4 equal slices, what fraction did I eat? 🍕' },
    ],
  },
  {
    id: 'sky',
    title: 'Why is the sky blue?',
    tag: null,
    messages: [
      {
        role: 'assistant',
        content:
          'Great question! Short answer: the air scatters blue light the most. Want the longer story, with the sunset twist?',
      },
      { role: 'user', content: 'yes!! with the sunset twist' },
      { role: 'assistant', content: 'So imagine light as a rainbow of tiny waves… ✨' },
    ],
  },
]
