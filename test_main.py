
import unittest
import os
import time

# Importa o módulo principal que vamos testar
import main

# --- Configuração do Teste ---
# IMPORTANTE: Este baralho será usado para os testes. Ele será criado se não existir.
TEST_DECK_NAME = "DECK_TESTE"

class TestAnkiUpdaterIntegration(unittest.TestCase):

    created_note_ids = []

    @classmethod
    def setUpClass(cls):
        """Configuração inicial antes de todos os testes."""
        print("\n--- Iniciando Testes de Integração ---")
        print(f"Usando baralho de teste: {TEST_DECK_NAME}")
        
        # Apenas remove o handler do console para não poluir a saída do teste,
        # mas mantém o FileHandler para que possamos depurar o log se necessário.
        cls.original_handlers = main.logging.root.handlers[:]
        main.logging.root.handlers = [h for h in main.logging.root.handlers if not isinstance(h, main.logging.StreamHandler)]

        # Verifica se a chave de API está disponível
        if not os.getenv("GEMINI_API_KEY"):
            raise unittest.SkipTest("A variável de ambiente GEMINI_API_KEY não está definida. Pulando testes de integração.")

        # Garante que o baralho de teste exista no Anki
        existing_decks = main.anki_request('deckNames')
        if existing_decks is not None and TEST_DECK_NAME not in existing_decks:
            print(f"Criando baralho de teste '{TEST_DECK_NAME}'...")
            main.anki_request('createDeck', deck=TEST_DECK_NAME)
        
        # Configura o script principal para usar o baralho de teste
        main.DECK_NAME = TEST_DECK_NAME

    @classmethod
    def tearDownClass(cls):
        """Limpeza final após todos os testes."""
        if cls.created_note_ids:
            print(f"\nLimpando {len(cls.created_note_ids)} notas de teste criadas...")
            main.anki_request('deleteNotes', notes=cls.created_note_ids)
        main.logging.root.handlers = cls.original_handlers
        print("--- Testes de Integração Finalizados ---")

    def test_01_anki_connection(self):
        """Verifica se a conexão com o AnkiConnect está ativa."""
        print("Executando test_01_anki_connection...")
        response = main.anki_request('deckNames')
        self.assertIsNotNone(response, "Não foi possível conectar ao AnkiConnect. O Anki está aberto e o addon está instalado?")
        self.assertIn(TEST_DECK_NAME, response)

    def test_02_create_and_update_basic_note(self):
        """Cria uma nota do tipo Básico, a atualiza e verifica o resultado."""
        print("Executando test_02_create_and_update_basic_note...")
        # 1. Adiciona uma nova nota do tipo Básico
        note_params = {
            "deckName": TEST_DECK_NAME,
            "modelName": "Básico",
            "fields": {
                "Frente": "The book is on the table.",
                "Verso": "O livro está sobre a mesa.",
                "GeneratedSentence": "",
                "GeneratedTranslation": "",
                "GeneratedIvl": ""
            },
            "tags": ["teste_integracao"]
        }
        note_id = main.anki_request('addNote', note=note_params)
        self.assertIsNotNone(note_id, "Falha ao criar a nota de teste do tipo Básico.")
        self.created_note_ids.append(note_id)

        # Força o cartão a ser "devido" para que o script o encontre
        card_ids = main.anki_request('findCards', query=f'nid:{note_id}')
        main.anki_request('reposition', cards=card_ids, start=0)

        # 2. Executa a lógica principal de atualização
        main.main()

        # 3. Verifica se a nota foi atualizada
        note_info = main.anki_request('notesInfo', notes=[note_id])[0]
        generated_sentence = note_info['fields'][main.FIELD_MAPPINGS["Básico"]["generated_sentence"]]['value']
        generated_translation = note_info['fields'][main.FIELD_MAPPINGS["Básico"]["generated_translation"]]['value']

        self.assertIn("<span class=\"lookup\"", generated_sentence, "O campo da frase gerada não contém o HTML esperado.")
        self.assertTrue(len(generated_translation) > 0, "O campo da tradução gerada está vazio.")
        print("Nota do tipo Básico atualizada com sucesso.")

    def test_03_check_interval_skip(self):
        """Verifica se uma nota já atualizada para o intervalo atual é pulada."""
        print("Executando test_03_check_interval_skip...")
        # Usa a nota criada no teste anterior, que já foi atualizada
        note_id = self.created_note_ids[-1]
        
        # Pega o conteúdo atual para comparação
        note_info_before = main.anki_request('notesInfo', notes=[note_id])[0]
        sentence_before = note_info_before['fields'][main.FIELD_MAPPINGS["Básico"]["generated_sentence"]]['value']

        print("Executando main() novamente para verificar se a nota será pulada...")
        # Executa o main de novo. Como o intervalo não mudou, a nota deve ser pulada.
        main.main()

        note_info_after = main.anki_request('notesInfo', notes=[note_id])[0]
        sentence_after = note_info_after['fields'][main.FIELD_MAPPINGS["Básico"]["generated_sentence"]]['value']

        self.assertEqual(sentence_before, sentence_after, "A nota foi atualizada novamente, mas deveria ter sido pulada.")
        print("Verificação de pular nota por intervalo concluída com sucesso.")


if __name__ == '__main__':
    # Isso permite executar os testes diretamente do terminal com `python test_main.py`
    unittest.main()
