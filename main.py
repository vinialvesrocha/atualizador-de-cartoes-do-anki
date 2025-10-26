import requests
import os
import time
import json
import google.generativeai as genai
from tqdm import tqdm
import logging
from google.api_core import exceptions

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
GEMINI_MODEL = "gemini-2.5-flash"
REQUEST_DELAY_SECONDS = 2

# --- Mapeamento de Campos do Anki (Personalize para seus tipos de nota) ---
FIELD_MAPPINGS = {
    "YTLearner-Advanced": {
        "term": "Term",
        "meaning": "Meaning",
        "original_sentence": "ExampleSentence",
        "full_translation": "ExampleTranslation",
        "generated_sentence": "GeneratedSentence",
        "generated_translation": "GeneratedTranslation",
        "generated_interval": "GeneratedIvl"
    },
    "Basic": { # Aplica-se a "Basic" e "Básico"
        "front": "Front",
        "back": "Back",
        "generated_sentence": "GeneratedSentence",
        "generated_translation": "GeneratedTranslation",
        "generated_interval": "GeneratedIvl"
    },
    "Básico": { # Garante compatibilidade
        "front": "Frente",
        "back": "Verso",
        "generated_sentence": "GeneratedSentence",
        "generated_translation": "GeneratedTranslation",
        "generated_interval": "GeneratedIvl"
    }
}

# --- Gerenciamento de Chaves de API ---
API_KEYS = ["AIzaSyCpHH7k1v5M7_3im0_MARj0m2X4ToKkTuc", "AIzaSyAAfy-TkFamePJM0y1k8ANCNRdGqPpRL4A", "AIzaSyDpBN1p4XrqXY3tCA3f6j_dKjt1E66gXAE", "AIzaSyBaMtKxTt07DIFkqJD_IE6pIR6Q7gh_9a8", "AIzaSyBigmLPFiXQdIEUGY6ufnO20NFhtij4GAE", "AIzaSyAMQt7r3oLX3ONQD4jDaJpEr6O8ceMFhHI", "AIzaSyAEQxzGxpP18zCNeOKNTy61gzxbRnTiPGA", "AIzaSyDwiPbK_eV150uJ9IqyoMIXQRIblrK8msM"]
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
    except exceptions.ResourceExhausted as e:
        logging.warning(f"API Key #{CURRENT_KEY_INDEX + 1} has reached its limit (ResourceExhausted). {e}")
        CURRENT_KEY_INDEX += 1
        if switch_to_key(CURRENT_KEY_INDEX):
            logging.info("Retrying with the new key...")
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
            # This path is taken if all keys are exhausted
            return None
    except Exception as e:
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


def generate_new_content(term=None, meaning=None, original_sentence=None, full_translation=None):
    """Gera novo conteúdo para o cartão em uma única chamada de API, preservando o significado original do termo."""
    
    prompt_context = ""
    # Caso 1: O termo é fornecido diretamente (ex: notas YTLearner-Advanced)
    if term and meaning and original_sentence and full_translation:
        logging.info(f"Generating new content for term: '{term}'")
        prompt_context = f"The student is studying the key word/phrase: '{term}' and the meaning em"
        f"Brazilian Portuguese is '{meaning}'.\n"
        f"Their current example sentence is: '{original_sentence}' and And its Brazilian Portuguese translation is:"
        f"'{full_translation}'.\n\n"
        "Then, perform the following three tasks:\n"

    # Caso 2: A sentença original e sua tradução são fornecidas (ex: notas Basic)
    elif original_sentence and full_translation:
        logging.info(f"Generating new content from sentence: '{original_sentence}'")
        prompt_context = (
            f"A student's flashcard has the English sentence: '{original_sentence}'\n"
            f"And its Brazilian Portuguese translation is: '{full_translation}'.\n\n"
            "First, identify the single most important key word or short phrase from the English sentence that is the focus of study (it's often in bold). Let's call this the 'term'.\n"
            "Second, identify the corresponding Brazilian Portuguese translation of that 'term' within the provided full translation. Let's call this the 'term_translation'.\n"
            "Then, using that identified 'term' and its specific meaning given by 'term_translation', perform the following three tasks:\n"
        )
    else:
        logging.error("generate_new_content requires either a term or an original_sentence and full_translation.")
        return None, None

    prompt_tasks = (
        "1. Create a completely new, different, and natural English sentence that uses the 'term' with the **exact same meaning** as implied by its original context/translation.\n"
        "2. Translate that new English sentence into Brazilian Portuguese.\n"
        "3. Create a final HTML version of the new English sentence. In this HTML: a) The 'term' itself must be wrapped in a `<b>` tag. b) Every other word must be wrapped in a '<span class=\"lookup\" data-translation=\"...\">` tag, where the `data-translation` attribute contains the specific Brazilian Portuguese translation of that word in the sentence context. Remember to escape any double quotes inside the data-translation attribute as `&quot;`.\n\n"
        "**Important**: Your response MUST be a single valid JSON object with no extra text or explanations. The JSON object should have the following keys:\n"
        '- `"html_sentence"`: The final, complete HTML sentence with all tags.\n'
        '- `"translation"`: The plain text Brazilian Portuguese translation of the new sentence.\n'
        "Example format:\n"
        "{\n"
        '  \"html_sentence\": \"<span class=\"lookup\" data-translation=\"Ele\">He</span> <span class=\"lookup\" data-translation=\"tentou\">tried</span> <span class=\"lookup\" data-translation=\"a\">to</span> <b>wield</b> <span class=\"lookup\" data-translation=\"a\">the</span> <span class=\"lookup\" data-translation=\"antiga\">ancient</span> <span class=\"lookup\" data-translation=\"espada\">sword</span>.\",\n'
        '  \"translation\": \"Ele tentou empunhar a espada antiga.\"\n'
        "}"
    )

    prompt = prompt_context + prompt_tasks
    response_text = generative_request_with_retry(prompt)

    if not response_text:
        logging.error("Failed to get a response from the API.")
        return None, None

    try:
        clean_response = response_text.strip().replace("```json", "").replace("```", "").strip()
        response_data = json.loads(clean_response)
        html_sentence = response_data["html_sentence"]
        translation_part = response_data["translation"]
    except (json.JSONDecodeError, KeyError) as e:
        logging.error(f"Failed to decode JSON or find keys in API response. Error: {e}. Response: '{response_text}'")
        return None, None

    logging.info(f"Generated HTML: {html_sentence}")
    logging.info(f"Generated Translation: '{translation_part}'")

    return html_sentence, translation_part

# --- Funções de Atualização do Anki ---
def update_note_fields(note_id, model_name, sentence, translation, generated_interval):
    """Atualiza uma nota no Anki com a nova frase, tradução e o intervalo de geração."""
    logging.info(f"Updating note {note_id} with new sentence, translation, and interval: {generated_interval}.")

    field_map = FIELD_MAPPINGS.get(model_name)
    if not field_map:
        logging.error(f"No field mapping found for model: {model_name}. Cannot update note {note_id}.")
        return

    update_payload = {
        "note": {
            "id": note_id,
            "fields": {
                field_map["generated_sentence"]: sentence,
                field_map["generated_translation"]: translation,
                field_map["generated_interval"]: str(generated_interval)
            }
        }
    }
    anki_request("updateNoteFields", **update_payload)

# --- Main ---
def main():
    logging.info("--- Starting Anki Updater Script ---")
    if not setup_api_keys():
        return

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
            model_name = note["modelName"]
            pbar.set_postfix_str(f"Processing {note_id} ({model_name})")

            if CURRENT_KEY_INDEX >= len(API_KEYS):
                logging.error("Stopping script: All API keys have been exhausted.")
                break

            field_map = FIELD_MAPPINGS.get(model_name)
            if not field_map:
                logging.warning(f"Skipping note {note_id} with unhandled model type: {model_name}")
                pbar.update(1)
                continue

            # --- Lógica da Abordagem 3: Verificação de Intervalo ---
            card_ids = note.get("cards")
            if not card_ids:
                logging.warning(f"Skipping note {note_id} as it has no associated cards.")
                pbar.update(1)
                continue

            card_info_list = anki_request("cardsInfo", cards=card_ids)
            if not card_info_list:
                logging.warning(f"Skipping note {note_id}, could not fetch card info.")
                pbar.update(1)
                continue
            
            card_info = card_info_list[0]
            current_interval = card_info['interval']
            
            fields = note["fields"]
            generated_interval_field = field_map.get("generated_interval", "GeneratedIvl") # Fallback
            generated_interval_str = fields.get(generated_interval_field, {}).get("value", "")

            try:
                generated_interval = int(generated_interval_str) if generated_interval_str else -1
            except (ValueError, TypeError):
                generated_interval = -1

            if current_interval == generated_interval:
                logging.info(f"Skipping note {note_id}: already has a fresh sentence for the current interval ({current_interval}).")
                pbar.update(1)
                continue
            # --- Fim da Lógica da Abordagem 3 ---

            logging.info(f"Processing note ID: {note_id}, Model: {model_name}, Interval: {current_interval}")

            new_sentence, new_translation = None, None

            if model_name == "YTLearner-Advanced":
                term = fields.get(field_map["term"], {}).get("value")
                meaning = fields.get(field_map["meaning"], {}).get("value")
                original_sentence = fields.get(field_map["original_sentence"], {}).get("value")
                full_translation = fields.get(field_map["full_translation"], {}).get("value")
                if term and original_sentence and full_translation:
                    new_sentence, new_translation = generate_new_content(term=term, meaning=meaning, original_sentence=original_sentence, full_translation=full_translation)
                else:
                    logging.warning(f"Skipping note {note_id} due to missing required fields for {model_name}.")

            elif model_name in ["Basic", "Básico"]:
                front_field = field_map.get("front", "Front") # Fallback
                back_field = field_map.get("back", "Back") # Fallback
                original_sentence = fields.get(front_field, {}).get("value")
                translation = fields.get(back_field, {}).get("value")
                if original_sentence and translation:
                    new_sentence, new_translation = generate_new_content(original_sentence=original_sentence, full_translation=translation)
                else:
                    logging.warning(f"Skipping note {note_id} due to missing required fields for {model_name}.")
            
            else:
                # Este caso já é tratado pelo primeiro `if not field_map`
                pbar.update(1)
                continue

            if new_sentence and new_translation:
                update_note_fields(note_id, model_name, new_sentence, new_translation, current_interval)
                updated_count += 1
                pbar.set_postfix_str(f"Successfully updated {note_id}")
            else:
                logging.warning(f"Failed to generate new content for note {note_id}.")
            
            pbar.update(1)
            if i < total_notes - 1:
                pbar.set_postfix_str(f"Waiting for {REQUEST_DELAY_SECONDS}s...")
                time.sleep(REQUEST_DELAY_SECONDS)

    logging.info("--- Script Finished ---")
    logging.info(f"Total notes processed: {total_notes}")
    logging.info(f"Successfully updated notes: {updated_count}")

if __name__ == "__main__":
    main()
