import requests
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
GEMINI_MODEL = "gemini-1.0-pro"
REQUEST_DELAY_SECONDS = 10

# --- Gerenciamento de Chaves de API ---
API_KEYS = []
CURRENT_KEY_INDEX = 0

def setup_api_keys():
    """Carrega as chaves de API da variável de ambiente."""
    global API_KEYS, CURRENT_KEY_INDEX
    keys_string = os.getenv("GEMINI_API_KEY")
    if not keys_string:
        logging.error("Fatal: GEMINI_API_KEY environment variable not found.")
        return False

    API_KEYS = [key.strip() for key in keys_string.split(',')]
    if not API_KEYS:
        logging.error("Fatal: No API keys found in GEMINI_API_KEY.")
        return False

    logging.info(f"Loaded {len(API_KEYS)} API key(s).")
    CURRENT_KEY_INDEX = 0
    return switch_to_key(CURRENT_KEY_INDEX)

def switch_to_key(key_index):
    """Muda para uma chave de API específica e a configura."""
    if key_index >= len(API_KEYS):
        logging.error("All API keys have reached their usage limit.")
        return False

    try:
        api_key = API_KEYS[key_index]
        genai.configure(api_key=api_key)
        logging.info(f"Switched to API Key #{key_index + 1}.")
        return True
    except Exception as e:
        logging.error(f"Failed to configure API Key #{key_index + 1}: {e}")
        return False

def verify_model_availability():
    """Verifica se o modelo configurado está disponível para a chave de API atual."""
    try:
        available_models = [m.name for m in genai.list_models()]

        # O nome do modelo na API pode ser 'models/gemini-1.0-pro'
        if f'models/{GEMINI_MODEL}' in available_models:
            logging.info(f"Model '{GEMINI_MODEL}' is available.")
            return True
        else:
            logging.error(f"Model '{GEMINI_MODEL}' is not available for your API key.")
            logging.error("Please choose one of the available models and update the GEMINI_MODEL variable in the script.")
            # Filtra e formata a lista de modelos relevantes para geração de conteúdo
            generative_models = [name.replace('models/', '') for name in available_models if 'generateContent' in genai.get_model(name).supported_generation_methods]
            logging.error(f"Available generative models: {generative_models}")
            return False
    except Exception as e:
        logging.error(f"Could not verify model availability. Error: {e}")
        return False

def generative_request_with_retry(prompt):
    """Faz uma requisição à API Gemini com lógica de troca de chave em caso de erro de cota."""
    global CURRENT_KEY_INDEX

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        if "429" in str(e) and "resource has been exhausted" in str(e).lower():
            logging.warning(f"API Key #{CURRENT_KEY_INDEX + 1} has reached its limit.")
            CURRENT_KEY_INDEX += 1
            if switch_to_key(CURRENT_KEY_INDEX):
                logging.info("Retrying with the new key...")
                # Após trocar a chave, precisamos verificar o modelo novamente com a nova chave.
                if not verify_model_availability():
                    return None
                try:
                    model = genai.GenerativeModel(GEMINI_MODEL)
                    response = model.generate_content(prompt)
                    return response.text.strip()
                except Exception as retry_e:
                    logging.error(f"Retry failed with new key: {retry_e}")
                    return None
            else:
                return None
        else:
            logging.error(f"An unexpected error occurred with the Gemini API: {e}")
            return None

# --- Funções Anki ---
def anki_request(action, **params):
    payload = {"action": action, "version": 6, "params": params}
    try:
        response = requests.post(ANKI_CONNECT_URL, json=payload)
        response.raise_for_status()
        response_json = response.json()
        if response_json.get("error"):
            logging.error(f"AnkiConnect error for action '{action}': {response_json['error']}")
            return None
        return response_json.get("result")
    except requests.exceptions.RequestException as e:
        logging.error(f"Could not connect to AnkiConnect at {ANKI_CONNECT_URL}. Is Anki running? Error: {e}")
        return None

def fetch_due_notes():
    logging.info(f"Searching for due notes in deck '{DECK_NAME}'...")
    note_ids = anki_request("findNotes", query=f"deck:{DECK_NAME} is:due")
    return note_ids if note_ids is not None else []

def get_note_details(note_ids):
    logging.info(f"Fetching details for {len(note_ids)} notes...")
    return anki_request("notesInfo", notes=note_ids) or []

# --- Funções de Geração de Conteúdo ---
def generate_term(original_sentence, translation):
    logging.info(f"Generating term from sentence: '{original_sentence}'")
    prompt = (
        f"Given the English sentence: '{original_sentence}'\n"
        f"And its Portuguese translation: '{translation}'\n\n"
        "Identify the single most important key word or short phrase from the English sentence that is the focus of study. "
        "Return ONLY the key word or phrase, with no extra text or explanations."
    )
    term = generative_request_with_retry(prompt)
    if term:
        logging.info(f"Generated term: '{term}'")
    return term

def generate_sentence(term, original_sentence):
    logging.info(f"Generating a new sentence for term: '{term}'")
    prompt = (
        f"You are an English teacher creating a new example sentence for a student.\n"
        f"The student is studying the key word/phrase: '{term}'\n"
        f"Their current example sentence is: '{original_sentence}'\n\n"
        f"Create a completely new, different English sentence that also uses '{term}' correctly. "
        "The new sentence should be natural and easy to understand. Do not repeat the original sentence."
    )
    new_sentence = generative_request_with_retry(prompt)
    if new_sentence:
        logging.info(f"Generated new sentence: '{new_sentence}'")
    return new_sentence

# --- Funções de Atualização do Anki ---
def update_generated_sentence(note_id, new_sentence):
    logging.info(f"Updating note {note_id} with new sentence.")
    update_payload = {"note": {"id": note_id, "fields": {"GeneratedSentence": new_sentence}}}
    if anki_request("updateNoteFields", **update_payload) is None:
        logging.error(f"Failed to update note {note_id}.")

# --- Main ---
def main():
    logging.info("--- Starting Anki Updater Script ---")
    if not setup_api_keys():
        return

    # Verifica a disponibilidade do modelo antes de começar
    if not verify_model_availability():
        return

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
            pbar.set_postfix_str(f"Processing {note_id}")

            if CURRENT_KEY_INDEX >= len(API_KEYS):
                logging.error("Stopping script: All API keys have been exhausted.")
                break

            model_name = note["modelName"]
            fields = note["fields"]
            
            logging.info(f"Processing note ID: {note_id}, Model: {model_name}")

            term, original_sentence = None, None

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
                logging.warning(f"Skipping note {note_id} due to missing fields or term extraction failure.")
                pbar.update(1)
                continue

            if "GeneratedSentence" not in fields:
                 logging.warning(f"Skipping note {note_id}: 'GeneratedSentence' field not found. Please add it to the '{model_name}' Note Type in Anki.")
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

    logging.info("--- Script Finished ---")
    logging.info(f"Total notes processed: {total_notes}")
    logging.info(f"Successfully updated notes: {updated_count}")

if __name__ == "__main__":
    main()
