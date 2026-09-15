import os
import shutil
import time
from pathlib import Path

from gradio_client import Client


SPACE_ID = "mrfakename/minimax-h3-ultra-fast"

HF_TOKEN = os.getenv("HF_TOKEN")

OUTPUT_DIR = Path(
    os.getenv("OUTPUT_DIR", "/tmp/videos")
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


class HFGenerationError(Exception):

    def __init__(
        self,
        message,
        error_type="unknown"
    ):
        super().__init__(message)
        self.error_type = error_type


def create_client():
    """
    Tworzy klienta Gradio dla MiniMax H3 Ultra Fast.
    """

    if HF_TOKEN:
        return Client(
            SPACE_ID,
            token=HF_TOKEN,
        )

    return Client(SPACE_ID)


def get_output_path(job_id, scene_number):

    return (
        OUTPUT_DIR
        / f"{job_id}_scene_{scene_number}.mp4"
    )


def extract_file_path(result):

    if not result:
        raise HFGenerationError(
            "MiniMax zwrócił pusty wynik."
        )

    video = result[0]

    if hasattr(video, "path"):

        path = video.path

        if path:
            return Path(path)

    if isinstance(video, dict):

        path = video.get("path")

        if path:
            return Path(path)

    if isinstance(video, str):
        return Path(video)

    raise HFGenerationError(
        f"Nieznany format wyniku MiniMax: "
        f"{type(video)}"
    )


def generate_video(
    prompt,
    job_id,
    scene_number,
    duration=14,
):
    """
    Generuje jeden klip.

    Dla naszego pipeline'u używamy maksymalnie
    14 sekund na pojedynczą generację.
    """

    if not prompt:
        raise HFGenerationError(
            "Brak promptu."
        )

    duration = min(
        float(duration),
        14.0
    )

    canvas = "544x960 · 9:16 fast"

    client = create_client()

    print(
        f"[MiniMax] "
        f"start job={job_id} "
        f"scene={scene_number} "
        f"duration={duration}s",
        flush=True,
    )

    try:

        job = client.submit(
            prompt=prompt,
            image_path=None,
            last_image_path=None,
            canvas=canvas,
            duration=duration,
            steps=4,
            seed=42,
            upsample=False,
            acceleration="Exact",
            lora_preset="Turbo · 4 steps",
            lora_repo="",
            lora_filename="",
            lora_strength=1.0,
            generation_preset=(
                "Turbo 4-step — fastest, "
                "more artifacts"
            ),
            references=None,
            api_name="/generate",
        )

    except Exception as exc:

        text = str(exc).lower()

        if (
            "429" in text
            or "quota" in text
            or "rate limit" in text
        ):
            raise HFGenerationError(
                str(exc),
                "rate_limit"
            ) from exc

        if (
            "503" in text
            or "queue" in text
            or "busy" in text
        ):
            raise HFGenerationError(
                str(exc),
                "server_busy"
            ) from exc

        raise HFGenerationError(
            str(exc),
            "submit"
        ) from exc

    # Czekamy na zakończenie zadania.
    # Nie utrzymujemy własnego HTTP requestu.

    while not job.done():

        try:

            status = job.status()

            rank = getattr(
                status,
                "rank",
                None
            )

            queue_size = getattr(
                status,
                "queue_size",
                None
            )

            eta = getattr(
                status,
                "eta",
                None
            )

            code = getattr(
                status,
                "code",
                None
            )

            print(
                f"[MiniMax] "
                f"scene={scene_number} "
                f"status={code} "
                f"queue={rank}/{queue_size} "
                f"eta={eta}",
                flush=True,
            )

        except Exception:
            pass

        time.sleep(5)

    try:

        result = job.result()

    except Exception as exc:

        text = str(exc).lower()

        if (
            "429" in text
            or "quota" in text
            or "rate limit" in text
        ):
            raise HFGenerationError(
                str(exc),
                "rate_limit"
            ) from exc

        if (
            "503" in text
            or "busy" in text
        ):
            raise HFGenerationError(
                str(exc),
                "server_busy"
            ) from exc

        raise HFGenerationError(
            str(exc),
            "generation"
        ) from exc

    source_file = extract_file_path(
        result
    )

    if not source_file.exists():

        raise HFGenerationError(
            f"Plik wynikowy nie istnieje: "
            f"{source_file}",
            "download"
        )

    output_file = get_output_path(
        job_id,
        scene_number,
    )

    shutil.copy2(
        source_file,
        output_file,
    )

    print(
        f"[MiniMax] "
        f"scene={scene_number} GOTOWA: "
        f"{output_file}",
        flush=True,
    )

    return output_file

Teraz podział odpowiedzialności jest czysty:

- MiniMax Worker → generuje klip do 14 s.
- Railway → czeka na kolejkę i wynik.
- Kombajner → decyduje, jak powstaje finalne 15 s.
- Audio → nie zakładamy niczego po stronie generatora; kombajner może wykorzystać/dodać audio zgodnie z Twoim obecnym pipeline'em.

Czyli żadnych ukrytych założeń o długości ani audio w tym module.
