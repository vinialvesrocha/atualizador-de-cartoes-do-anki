import requests
import json
import os
import time
import google.generativeai as genai
from tqdm import tqdm
import logging

# --- Configuração do Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("anki_updater.log", mode='w'),
        logging.StreamHandler()
    ]
)

# --- Configurações ---
ANKI_CONNECT_URL = "http://localhost:8765"
DECK_NAME = "ENGLISH-A2"
GEMINI_MODEL = "gemini-1.5-flash"  # Modelo de IA a ser usado
REQUEST_DELAY_SECONDS = 10  # Pausa em segundos entre cada nota

try:
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables.")
    genai.configure(api_key=gemini_api_key)
    logging.info("Google AI SDK configured successfully.")
except (ValueError, Exception) as e:
    logging.error(f"Fatal error during Gemini configuration: {e}")
    exit()


# --- Funções ---
def anki_request(action, **params):
    """Função genérica para fazer requisições ao AnkiConnect."""
    payload = {"action": action, "version": 6, "params": params}
    try:
        response = requests.post(ANKI_CONNECT_URL, json=payload)
        response.raise_for_status()  # Lança um erro para status HTTP 4xx/5xx
        response_json = response.json()
        if response_json.get("error"):
            logging.error(f"AnkiConnect error for action '{action}': {response_json['error']}")
            return None
        return response_json.get("result")
    except requests.exceptions.RequestException as e:
        logging.error(f"Could not connect to AnkiConnect at {ANKI_CONNECT_URL}. Is Anki running with AnkiConnect? Error: {e}")
        return None


def fetch_due_notes():
    """Busca todas as notas do deck que possuem cartões devidos hoje."""
    logging.info(f"Searching for due notes in deck '{DECK_NAME}'...")
    query = f"deck:{DECK_NAME} is:due"
    note_ids = anki_request("findNotes", query=query)
    if note_ids is None:
        logging.error("Failed to fetch due notes.")
    return note_ids if note_ids is not None else []


def get_note_details(note_ids):
    """Obtém detalhes de uma lista de notas pelo ID."""
    logging.info(f"Fetching details for {len(note_ids)} notes...")
    notes_info = anki_request("notesInfo", notes=note_ids)
    return notes_info if notes_info is not None else []


def generate_term(original_sentence, translation):
    """Extrai o termo principal da frase usando IA."""
    logging.info(f"Generating term from sentence: '{original_sentence}'")
    model = genai.GenerativeModel(GEMINI_MODEL)
    prompt = (
        f"Given the English sentence: '{original_sentence}'\n"
        f"And its Portuguese translation: '{translation}'\n\n"
        "Identify the single most important key word or short phrase from the English sentence that is the focus of study. "
        "Return ONLY the key word or phrase, with no extra text or explanations."
    )
    try:
        response = model.generate_content(prompt)
        term = response.text.strip()
        logging.info(f"Generated term: '{term}'")
        return term
    except Exception as e:
        logging.error(f"Error generating term for sentence '{original_sentence}': {e}")
        return None


def generate_sentence(term, original_sentence):
    """Gera uma nova frase para um termo, baseada na original."""
    logging.info(f"Generating a new sentence for term: '{term}'")
    model = genai.GenerativeModel(GEMINI_MODEL)
    prompt = (
        f"You are an English teacher creating a new example sentence for a student.\n"
        f"The student is studying the key word/phrase: '{term}'\n"
        f"Their current example sentence is: '{original_sentence}'\n\n"
        f"Create a completely new, different English sentence that also uses '{term}' correctly. "
        "The new sentence should be natural and easy to understand. Do not repeat the original sentence."
    )
    try:
        response = model.generate_content(prompt)
        new_sentence = response.text.strip()
        logging.info(f"Generated new sentence: '{new_sentence}'")
        return new_sentence
    except Exception as e:
        logging.error(f"Error generating sentence for term '{term}': {e}")
        return None


def add_generated_sentence_field(note_id):
    """Adiciona o campo GeneratedSentence ao modelo da nota, caso não exista."""
    logging.warning(f"Field 'GeneratedSentence' not found in note {note_id}. Attempting to add it.")
    # Esta função é um paliativo. A melhor forma é o usuário adicionar o campo manualmente no Anki.
    # A API do AnkiConnect não tem uma forma direta de adicionar campos, apenas de atualizar.
    # Esta chamada vai falhar se o campo não existir, mas registramos o aviso.
    update_payload = {
        "note": {
            "id": note_id,
            "fields": {"GeneratedSentence": " "} # Adiciona um espaço para inicializar
        }
    }
    result = anki_request("updateNoteFields", **update_payload)
    if result is None:
        logging.error(f"Could not add/update 'GeneratedSentence' field for note {note_id}. Please add it manually to your Note Type in Anki.")

def update_generated_sentence(note_id, new_sentence):
    """Atualiza o campo GeneratedSentence de uma nota no Anki."""
    logging.info(f"Updating note {note_id} with new sentence.")
    update_payload = {
        "note": {
            "id": note_id,
            "fields": {"GeneratedSentence": new_sentence}
        }
    }
    result = anki_request("updateNoteFields", **update_payload)
    if result is None:
        logging.error(f"Failed to update note {note_id}.")

# --- Main ---
def main():
    logging.info("--- Starting Anki Updater Script ---")
    note_ids = fetch_due_notes()
    if not note_ids:
        logging.info("No due notes found today or failed to fetch notes. Exiting.")
        return

    logging.info(f"Found {len(note_ids)} due notes.")
    notes = get_note_details(note_ids)
    if not notes:
        logging.error("Could not fetch note details. Exiting.")
        return

    updated_count = 0
    total_notes = len(notes)

    with tqdm(total=total_notes, desc="Updating Anki Cards") as pbar:
        for i, note in enumerate(notes):
            note_id = note["noteId"]
            model_name = note["modelName"]
            fields = note["fields"]
            
            pbar.set_postfix_str(f"Processing {note_id} ({model_name})")
            logging.info(f"Processing note ID: {note_id}, Model: {model_name}, Fields: {list(fields.keys())}")

            term = None
            original_sentence = None

            if model_name == "YTLearner-Advanced":
                term = fields.get("Term", {}).get("value")
                original_sentence = fields.get("ExampleSentence", {}).get("value")
            elif model_name in ["Basic", "Básico"]:
                front_field = "Front" if "Front" in fields else "Frente"
                back_field = "Back" if "Back" in fields else "Verso"
                original_sentence = fields.get(front_field, {}).get("value")
                translation = fields.get(back_field, {}).get("value")
                if original_sentence and translation:
                    term = generate_term(original_sentence, translation)
            else:
                logging.warning(f"Skipping note {note_id} with unhandled model type: {model_name}")
                pbar.update(1)
                continue

            if not term or not original_sentence:
                logging.warning(f"Skipping note {note_id} due to missing required fields or term extraction failure.")
                pbar.update(1)
                continue

            # Verifica a existência do campo e tenta adicioná-lo se necessário
            if "GeneratedSentence" not in fields:
                add_generated_sentence_field(note_id)
                # Recarrega os detalhes da nota para ver se o campo foi adicionado
                note_details = get_note_details([note_id])
                if not note_details or "GeneratedSentence" not in note_details[0]["fields"]:
                    logging.error(f"Skipping note {note_id} because 'GeneratedSentence' field could not be added or found.")
                    pbar.update(1)
                    continue

            new_sentence = generate_sentence(term, original_sentence)
            if new_sentence:
                update_generated_sentence(note_id, new_sentence)
                updated_count += 1
                pbar.set_postfix_str(f"Successfully updated {note_id}")
            else:
                logging.warning(f"Failed to generate a new sentence for note {note_id}.")
            
            pbar.update(1)
            if i < total_notes - 1:
                pbar.set_postfix_str(f"Waiting for {REQUEST_DELAY_SECONDS}s...")
                time.sleep(REQUEST_DELAY_SECONDS)

    logging.info(f"--- Script Finished ---")
    logging.info(f"Total notes processed: {total_notes}")
    logging.info(f"Successfully updated notes: {updated_count}")

if __name__ == "__main__":
    main()
