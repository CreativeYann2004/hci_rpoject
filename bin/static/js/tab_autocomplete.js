document.addEventListener("DOMContentLoaded", () => {
  const guessArtistInput = document.getElementById("guess");
  if (!guessArtistInput) return;
  guessArtistInput.addEventListener("keydown", async (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const query = guessArtistInput.value.trim();
      if (!query) return;
      try {
        const resp = await fetch(`/autocomplete/tab_artist?query=${encodeURIComponent(query)}`);
        const data = await resp.json();
        if (data.match) {
          guessArtistInput.value = data.match;
          guessArtistInput.focus();
          guessArtistInput.setSelectionRange(guessArtistInput.value.length, guessArtistInput.value.length);
        }
      } catch (err) {
        console.error(err);
      }
    }
  });
});
