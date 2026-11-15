"""An optional pygame window for watching a sim run in real time (`--live`).

Kept out of the default path so the headless demo and CI never import pygame.
"""

from __future__ import annotations

from typing import Any

from roboglot.sim2d.render import render_duckcam, render_topdown


class LiveWindow:
    def __init__(self, size: int = 256) -> None:
        try:
            import pygame
        except ImportError as e:  # pragma: no cover - optional extra
            raise ImportError("--live needs pygame: uv pip install 'roboglot[live]'") from e
        self.pygame = pygame
        self.size = size
        pygame.init()
        self.screen = pygame.display.set_mode((size * 2 + 4, size))
        pygame.display.set_caption("Roboglot sim2d — world | duck-cam")

    def draw(self, world: Any) -> None:
        pg = self.pygame
        for event in pg.event.get():
            if event.type == pg.QUIT:
                raise KeyboardInterrupt
        cam = render_duckcam(world, self.size)
        for img, x in ((render_topdown(world, self.size), 0), (cam, self.size + 4)):
            surf = pg.image.fromstring(img.tobytes(), img.size, "RGB")
            self.screen.blit(surf, (x, 0))
        pg.display.flip()

    def close(self) -> None:
        self.pygame.quit()
