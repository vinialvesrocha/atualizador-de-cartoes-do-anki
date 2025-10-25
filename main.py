import requests
import json
import os
import time
import google.generativeai as genai
from tqdm import tqdm

# --- Configurações ---
ANKI_CONNECT_URL = "http://localhost:8765"
DECK_NAME = "ENGLISH-A2"
GEMINI_MODEL = "gemini-2.0-flash-001" # Modelo de IA a ser usado
REQUEST_DELAY_SECONDS = 10 # Pausa em segundos entre cada nota
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# --- Funções ---
def fetch_due_notes():
    """Busca todas as notas do deck que possuem cartões devidos hoje."""
    payload = {
        "action": "findNotes",
        "version": 6,
        "params": {"query": f"deck:{DECK_NAME} is:due"}
    }
    response = requests.post(ANKI_CONNECT_URL, json=payload).json()
    return response.get("result", [])

def get_note_details(note_ids):
    """Obtém detalhes de uma lista de notas pelo ID."""
    payload = {"action": "notesInfo", "version": 6, "params": {"notes": note_ids}}
    response = requests.post(ANKI_CONNECT_URL, json=payload).json()
    return response.get("result", [])

def generate_term(original_sentence, translation):
    """Extrai o termo principal da frase usando IA."""
    model = genai.GenerativeModel(GEMINI_MODEL)
    prompt = (
        f"Given the English sentence: '{original_sentence}'\n"
        f"And its Portuguese translation: '{translation}'\n\n"
        "Identify the single most important key word or short phrase from the English sentence that is the focus of study. "
        "Return ONLY the key word or phrase, with no extra text or explanations."
    )
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Error generating term: {e}")
        return None

def generate_sentence(term, original_sentence):
    """Gera uma nova frase para um termo, baseada na original."""
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
        return response.text.strip()
    except Exception as e:
        print(f"Error generating sentence: {e}")
        return None

def add_generated_sentence_field(note_id):
    """Adiciona o campo GeneratedSentence ao modelo da nota, caso não exista."""
    payload = {
        "action": "updateNoteFields",
        "version": 6,
        "params": {
            "note": {
                "id": note_id,
                "fields": {"GeneratedSentence": ""}
            }
        }
    }
    try:
        requests.post(ANKI_CONNECT_URL, json=payload)
    except Exception as e:
        print(f"Could not add 'GeneratedSentence' field to note {note_id}: {e}")


def update_generated_sentence(note_id, new_sentence):
    """Atualiza o campo GeneratedSentence de uma nota no Anki."""
    payload = {
        "action": "updateNoteFields",
        "version": 6,
        "params": {
            "note": {
                "id": note_id,
                "fields": {"GeneratedSentence": new_sentence}
            }
        }
    }
    requests.post(ANKI_CONNECT_URL, json=payload)

# --- Main ---
def main():
    print("Fetching due notes...")
    note_ids = fetch_due_notes()
    if not note_ids:
        print("No due notes found today.")
        return

    print(f"Found {len(note_ids)} due notes. Fetching details...")
    notes = get_note_details(note_ids)
    updated_count = 0
    total_notes = len(notes)

    with tqdm(total=total_notes, desc="Updating Anki Cards") as pbar:
        for i, note in enumerate(notes):
            note_id = note["noteId"]
            model_name = note["modelName"]
            fields = note["fields"]
            
            term = None
            original_sentence = None

            pbar.set_postfix_str(f"Processing {note_id} ({model_name})")

            if model_name == "YTLearner-Advanced":
                term = fields.get("Term", {}).get("value")
                original_sentence = fields.get("ExampleSentence", {}).get("value")

            elif model_name == "Basic":
                original_sentence = fields.get("Front", {}).get("value")
                translation = fields.get("Back", {}).get("value")
                if original_sentence and translation:
                    term = generate_term(original_sentence, translation)

            elif model_name == "Básico":
                original_sentence = fields.get("Frente", {}).get("value")
                translation = fields.get("Verso", {}).get("value")
                if original_sentence and translation:
                    term = generate_term(original_sentence, translation)
            
            else:
                tqdm.write(f"Skipping note {note_id} with unhandled model type: {model_name}")
                pbar.update(1)
                continue

            if not term or not original_sentence:
                tqdm.write(f"Skipping note {note_id} due to missing required fields or term extraction failure.")
                pbar.update(1)
                continue

            if "GeneratedSentence" not in fields:
                tqdm.write(f"Field 'GeneratedSentence' not found. Attempting to add it to note {note_id}.")
                add_generated_sentence_field(note_id)

            try:
                new_sentence = generate_sentence(term, original_sentence)
                if new_sentence:
                    update_generated_sentence(note_id, new_sentence)
                    updated_count += 1
                    pbar.set_postfix_str(f"Successfully updated {note_id}")
                else:
                    tqdm.write(f"Failed to generate a new sentence for note {note_id}.")
            except Exception as e:
                tqdm.write(f"Failed to update note {note_id}: {e}")
            
            pbar.update(1)
            # Pausa para não exceder o limite da API
            if i < total_notes - 1: # Não esperar após o último item
                pbar.set_postfix_str(f"Waiting for {REQUEST_DELAY_SECONDS}s...")
                time.sleep(REQUEST_DELAY_SECONDS)


    print(f"\nDone! {updated_count} notes updated with new generated sentences.")

if __name__ == "__main__":
    main()