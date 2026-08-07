# 📓 Dziennik Pokładowy - Stan Projektu: Hybrydowy Portal Zwrotów

## 🎯 Cel główny
Stworzenie asynchronicznego, produkcyjnego systemu do obsługi zwrotów w e-commerce. Portal ma na celu automatyzację procesu obsługi zgłoszeń (Slot-Filling, RAG), redukcję kosztów zapytań LLM poprzez przechwytywanie powtarzalnych pytań (Semantic Router) oraz zachowanie kontroli nad nietypowymi zgłoszeniami dzięki modułowi akceptacji przez administratora (Human-In-The-Loop).

---

## 🧭 Decyzje architektoniczne (log ustaleń)

- **thread_id = ticket.id.** Jedna wartość pełni obie role: klucz główny w tabeli `tickets` (PostgreSQL) i identyfikator wątku w `AsyncPostgresSaver` (LangGraph). Świadome uproszczenie przy założeniu relacji 1:1 ticket↔wątek; brak obsługi scenariusza "klient wraca do tego samego zgłoszenia nową rozmową" — odłożone do rozważenia, jeśli pojawi się realna potrzeba.

- **Moment tworzenia ticketu.** Wiersz w `tickets` zakładany jest przy PIERWSZYM trafieniu wiadomości do grafu (Cache Miss w SemRouter lub wykryty wzorzec numeru zamówienia przez precheck), ze statusem `IN_PROGRESS` — nie dopiero po skompletowaniu wszystkich slotów. `ticket_id` (= `thread_id`) zwracany jest do klienta w odpowiedzi na pierwszą wiadomość i pełni rolę ID czatu. Frontend dołącza go do treści każdej kolejnej wiadomości w tej samej rozmowie — trzymany wyłącznie w pamięci widgetu na czas wizyty (zgodnie z zasadą braku trwałej sesji).

- **Czat bez trwałej sesji klienta** (natywny widget na stronie, jednorazowy — trwa w pamięci JS tylko przez czas wizyty, nie między wizytami). Konsekwencja: po zatwierdzeniu przez admina (HITL) system NIGDY nie wraca do okna czatu — finalizacja zawsze idzie e-mailem. `Node: Finalizacja/Mail` to jedyny kanał domknięcia sprawy po `interrupt_before`. Wynika z tego, że e-mail klienta jest obowiązkowym slotem zbieranym w `Node: Zbieranie Danych` — bez niego węzeł finalizujący nie ma gdzie wysłać odpowiedzi.

- **Pre-check numeru zamówienia** wykonywany jako osobna, deterministyczna funkcja PRZED `SemRouter` (nie jako warunek wewnątrz niego) — zero kosztu embeddingu/LLM na tym etapie, łatwa testowalność w izolacji (bez mocków ChromaDB/LLM).

- **Deterministyczny dostęp do danych w węzłach grafu (Wariant B).** `NodeVal` woła funkcje z `app/db/` i `app/rag/` bezpośrednio, jako zwykłe funkcje Pythona — NIE przez function calling LLM-a. Model nie decyduje, czy sprawdzić zamówienie w SQL czy regulamin w ChromaDB — to wykonywane jest zawsze, wynik trafia do modelu jako kontekst. Function calling / structured output LLM-a używane jest wyłącznie tam, gdzie potrzebna jest ekstrakcja lub formatowanie (Node: Zbieranie Danych, pole uzasadnienia w mailu), nigdy do decydowania o wykonaniu zapytań. Konsekwencja nazewnicza: katalog na funkcje SQL/RAG nazywa się `app/db/` i `app/rag/` (po źródle danych), NIE `tools/` — ta nazwa jest zarezerwowana semantycznie dla function-calling, którego tu nie stosujemy.

- **Reguły automatycznej kwalifikacji zwrotu (do rozstrzygnięcia w `NodeVal`).** Zgłoszenie kwalifikuje się do automatycznej realizacji (pomija HITL) tylko gdy spełnione są WSZYSTKIE warunki: (1) zakup nie starszy niż 14 dni, (2) kwota zwrotu ≤ 600 zł, (3) powód zwrotu znajduje się na zamkniętej liście dozwolonych powodów standardowych — **⚠️ TODO: pełna lista do uzupełnienia przez ucznia przed Fazą 4** (obecnie tylko przykład: "zbyt mały rozmiar"), (4) numer zamówienia został poprawnie zweryfikowany w bazie, (5) limit prób podania poprawnego numeru zamówienia nieprzekroczony (zob. niżej). Niespełnienie któregokolwiek warunku → `interrupt_before` → HITL.

- **Limit prób podania poprawnego numeru zamówienia.** Licznik `order_id_attempts` w stanie grafu, inkrementowany deterministycznie w kodzie (nie przez LLM) po każdej nieudanej weryfikacji w SQL. Po przekroczeniu progu (proponowana wartość startowa: **2 nieudane próby** — do potwierdzenia/dostosowania) sprawa automatycznie trafia do HITL. LLM odpowiada wyłącznie za konwersacyjną prośbę o korektę i ekstrakcję nowej wartości — decyzja o eskalacji to zwykły warunek w kodzie (`if order_id_attempts >= próg`), nie "rozumowanie" modelu. To NIE jest mechanizm agentowy, mimo że z perspektywy rozmowy wygląda na dynamiczny — to licznik i próg jak w każdej innej regule kwalifikacji.

- **Szablon e-maila finalizującego** ma być statyczny (stała struktura), LLM wypełnia wyłącznie pole z uzasadnieniem decyzji — nie generuje całej wiadomości od zera.

- **Endpoint admina do listowania zgłoszeń.** `GET /admin/tickets` zwraca zgłoszenia (domyślnie status `PENDING`, docelowo z możliwością filtrowania po statusie — zob. niżej `FAILED_DELIVERY`) — panel admina nigdy nie łączy się z bazą bezpośrednio, zawsze przez API (poprawione też na diagramie w README.md, gdzie wcześniej sugerował bezpośredni odczyt bazy).

- **Logi aplikacji — brak folderu `logs/` w repozytorium.** Zgodnie z zasadą traktowania logów jako strumienia zdarzeń (12-factor app), aplikacja loguje na stdout/stderr — infrastruktura (Docker, docelowo system agregacji typu Loki/CloudWatch) odpowiada za ich zbieranie. Struktura logu: poziom, timestamp, `thread_id` (gdzie dotyczy), kontekst operacji. Konfiguracja loggera (`app/logging_config.py`) odłożona do momentu realnej potrzeby (Faza 3/4), nie tworzona "na zapas" w Fazie 1.

- **Strategia obsługi błędów zewnętrznych zależności.** Postgres/ChromaDB: retry (kilka prób, krótkie odczekiwanie) wyłącznie w fazie startu aplikacji (`lifespan`) — zabezpieczenie przed wyścigiem startowym kontenerów Docker Compose. Poza startem (w trakcie obsługi żądań): fail-fast, natychmiastowy log z kontekstem (`thread_id`, operacja, wyjątek), bez automatycznego ponawiania — traktowane jako awaria systemowa wymagająca interwencji. API LLM: automatyczny retry z exponential backoff, max 3 próby w budżecie czasowym ~8-10s, stosowany WYŁĄCZNIE dla enumerowanej listy kodów błędów przejściowych (429, 5xx, timeout sieciowy); pozostałe kody (400/401/403/422 i inne błędy trwałe) idą od razu do fail-fast, bez próby ponawiania. Wspólna logika retry (np. przez bibliotekę `tenacity`) używana jednym mechanizmem we wszystkich wywołaniach LLM w projekcie, nie duplikowana per węzeł.

- **Idempotencja wysyłki e-maila (`Node: Finalizacja/Mail`).** Wywołanie LLM po tekst uzasadnienia jest bezstanowe i bezpieczne do retry. Faktyczna wysyłka e-maila NIE jest bezpiecznie retry'owalna wprost (ryzyko podwójnej wysyłki tej samej decyzji do klienta) — przed wysyłką węzeł sprawdza aktualny status ticketu w Postgresie; jeśli już `RESOLVED`, pomija ponowną wysyłkę. Status aktualizowany na `RESOLVED` dopiero PO potwierdzonym sukcesie wysyłki, nigdy przed.

- **Trwała awaria wysyłki po zatwierdzeniu przez admina.** Jeśli generowanie uzasadnienia lub wysyłka maila ostatecznie zawiedzie mimo wyczerpania prób retry, ticket NIE wraca automatycznie do kolejki `PENDING` (myliłoby to admina, sugerując nowe zgłoszenie do oceny, i mogłoby tworzyć pętlę przy trwałej awarii). Zamiast tego przyjmuje osobny status `FAILED_DELIVERY`, wymagający ręcznego ponowienia po stronie admina/operatora po ustaniu przyczyny awarii infrastruktury (np. dostawca poczty wrócił do działania).

---

## 📝 Lista zadań (Checklista)

### 🛠️ Faza 1: Konfiguracja i Środowisko
- [ ] Inicjalizacja repozytorium i struktury projektu (Python 3.12+).
- [ ] Utworzenie szkieletu katalogów zgodnie z ustaloną strukturą: `app/`, `app/db/`, `app/rag/`, `app/graph/`, `app/email/`, `data/`, `scripts/`, `tests/` (zob. README.md, sekcja "Struktura Projektu").
- [ ] Konfiguracja narzędzi do kontroli jakości kodu (Ruff, Mypy) w `pyproject.toml`.
- [ ] Przygotowanie pliku `docker-compose.yml` uruchamiającego TYLKO usługi stanowe (PostgreSQL 16, ChromaDB) do lokalnego developmentu — aplikacja FastAPI uruchamiana lokalnie przez `uvicorn --reload`, bez kontenera na tym etapie.

### 🗄️ Faza 2: Bazy Danych i Persystencja
- [ ] Utworzenie schematów i migracji dla relacyjnej bazy PostgreSQL (tabele biznesowe m.in. `orders`, `tickets`).
  - [ ] W `tickets`: kolumna identyfikatora pełni podwójną rolę jako `thread_id` (zob. Decyzje architektoniczne). Obowiązkowa kolumna na e-mail klienta (wymagana do finalizacji po HITL). Obowiązkowa kolumna statusu z wartościami min. `IN_PROGRESS`, `PENDING`, `RESOLVED`, `FAILED_DELIVERY` (zob. Decyzje architektoniczne — trwała awaria wysyłki).
- [ ] Konfiguracja puli połączeń (`asyncpg.Pool`) podpiętej pod cykl życia aplikacji (lifespan) FastAPI — z krótkim retry na wypadek wyścigu startowego kontenerów (zob. Decyzje architektoniczne).
- [ ] Wdrożenie wektorowej bazy danych ChromaDB.
- [ ] Utworzenie i zasilenie kolekcji ChromaDB:
  - [ ] `static_intents` (na potrzeby Semantic Routera).
  - [ ] `return_policy` (na potrzeby RAG i wektorów regulaminu zwrotów) — zasilana przez jednorazowy skrypt `scripts/ingest_policy.py`, czytający źródła z `data/`.
- [ ] Integracja checkpointera `AsyncPostgresSaver` dla zachowywania zserializowanych stanów grafu (LangGraph).

### 🌐 Faza 3: Warstwa API (Backend)
- [ ] Uruchomienie szkieletu aplikacji FastAPI (uruchamianej lokalnie przez `uvicorn --reload`).
- [ ] Przygotowanie modeli w Pydantic v2 (`app/schemas.py`) do rygorystycznej walidacji (format `order_id`, format e-maila, parsowanie JSON) — reużywanych jako schemat ekstrakcji danych dla LLM w węźle Zbieranie Danych.
- [ ] Implementacja głównego endpointu dla klientów: `POST /chat`. Kontrakt: opcjonalne pole `ticket_id` w request body — brak = pierwsza wiadomość (tworzy ticket), obecność = kontynuacja rozmowy.
- [ ] Implementacja endpointu dla administratora do listowania zgłoszeń: `GET /admin/tickets` (domyślnie status `PENDING`, z możliwością filtrowania — w tym po `FAILED_DELIVERY`).
- [ ] Implementacja endpointu dla administratora do zwalniania blokad (HITL): `POST /admin/tickets/{id}/resolve`.
- [ ] Implementacja endpointu do ręcznego ponowienia wysyłki maila dla ticketów ze statusem `FAILED_DELIVERY`: `POST /admin/tickets/{id}/retry-mail`.
- [ ] (Opcjonalnie, w razie potrzeby) Podstawowa konfiguracja loggera (`logging`) pisząca do stdout — NIE do pliku/folderu w repo (zob. Decyzje architektoniczne).

### 🧠 Faza 4: Inteligencja, Routing i Orkiestracja
- [ ] ⚠️ **Uzupełnić i spisać pełną listę dozwolonych "standardowych" powodów zwrotu** — wymagane przed implementacją reguł kwalifikacji poniżej.
- [ ] **Pre-check numeru zamówienia (przed routerem):**
  - [ ] Implementacja deterministycznej funkcji wykrywającej wzorzec numeru zamówienia w treści wiadomości klienta.
  - [ ] Rozdzielenie ścieżek: wzorzec obecny → pominięcie `SemRouter`, bezpośrednio do `Graph`; wzorzec nieobecny → standardowa ścieżka przez `SemRouter`.
- [ ] **Semantic Router:**
  - [ ] Konfiguracja modelu embeddingów (np. BGE).
  - [ ] Implementacja mechanizmu wyliczania dystansu (Match < 0.25 -> Cache Hit, w przeciwnym razie Cache Miss).
- [ ] **Warstwa dostępu do danych (`app/db/`, `app/rag/`):**
  - [ ] Funkcje SQL: tworzenie ticketu, aktualizacja statusu (w tym `FAILED_DELIVERY`), weryfikacja zamówienia, listowanie ticketów wg statusu.
  - [ ] Funkcje RAG: bezpośrednie wyszukiwanie w kolekcji `return_policy`.
  - [ ] Jawnie zaprojektowana obsługa wyjątków przy każdej funkcji (zob. Decyzje architektoniczne — strategia fail-fast/retry).
- [ ] **LangGraph App (Maszyna Stanów):**
  - [ ] Definicja stanu grafu (`app/graph/state.py`) — w tym licznik `order_id_attempts`.
  - [ ] Budowa węzła: `Node: Zbieranie Danych` (Slot-Filling z użyciem Function Calling/structured output). Obowiązkowe sloty: min. numer zamówienia, e-mail klienta. Przy błędnym numerze zamówienia: konwersacyjna prośba o korektę + inkrementacja licznika prób.
  - [ ] Budowa węzła: `Node: Walidacja & RAG` — deterministyczne (nie agentowe) wywołania funkcji z `app/db/` i `app/rag/`.
    - [ ] Implementacja reguł kwalifikacji "typowego" zwrotu (zob. Decyzje architektoniczne): czas od zakupu, próg kwotowy, lista powodów, poprawna weryfikacja zamówienia, limit prób.
  - [ ] Budowa węzła pauzy: `Node: interrupt_before` (Wstrzymanie grafu, gdy którakolwiek reguła kwalifikacji nie jest spełniona, na rzecz HITL).
  - [ ] Budowa węzła: `Node: Finalizacja/Mail` (Generowanie podsumowania/komunikatu do klienta, wysyłka e-mailem).
    - [ ] Zaprojektowanie statycznego szablonu maila (stała struktura + jedno zmienne pole na uzasadnienie decyzji generowane przez LLM).
    - [ ] Implementacja sprawdzenia statusu przed wysyłką (idempotencja) + aktualizacja na `RESOLVED` dopiero po potwierdzonym sukcesie (zob. Decyzje architektoniczne).
    - [ ] Obsługa trwałej awarii wysyłki: ustawienie statusu `FAILED_DELIVERY` po wyczerpaniu prób retry.
  - [ ] Start: wszystkie 4 węzły w jednym pliku `app/graph/nodes.py` — rozbicie na osobne pliki tylko jeśli złożoność (zwłaszcza `Walidacja & RAG`) wyraźnie urośnie.
  - [ ] Budowa i kompilacja grafu (`app/graph/builder.py`) z checkpointerem.
- [ ] Integracja z zewnętrznym silnikiem LLM (OpenAI / Anthropic / Gemini).
- [ ] Implementacja wspólnego mechanizmu retry (exponential backoff, enumerowana lista kodów błędów: 429/5xx/timeout → retry, 400/401/403/422 → fail-fast) dla wszystkich wywołań LLM w projekcie — jedna funkcja pomocnicza, nie duplikowana per węzeł.
- [ ] Opracowanie mechaniki wznawiania działania grafu (`graph.update_state()`) po obsłudze zdarzenia przez admina.

### 🧪 Faza 5: Testy i Utrzymanie (DevOps & CI/CD)
- [ ] Konfiguracja środowiska testowego asynchronicznego (`pytest`, `pytest-asyncio`, `pyproject.toml`).
- [ ] Zaimplementowanie testów dla endpointów API.
- [ ] Test jednostkowy dla funkcji pre-check numeru zamówienia (izolowany, bez mocków ChromaDB/LLM).
- [ ] Test jednostkowy dla logiki reguł kwalifikacji (14 dni / 600 zł / lista powodów / limit prób) — izolowany, sam moduł Python, bez sieci.
- [ ] Test jednostkowy dla logiki idempotencji wysyłki maila (mock statusu ticketu: czy pomija wysyłkę przy `RESOLVED`).
- [ ] Przygotowanie mocków dla zewnętrznych API (LLM) do testowania niezależnego od sieci, w tym scenariuszy błędów retry-worthy vs fail-fast.
- [ ] Weryfikacja stanu bazy danych i poprawności przepływów (flow) maszyny LangGraph.
- [ ] Przygotowanie docelowego `Dockerfile` dla aplikacji FastAPI (ewentualnie multi-stage build) gotowego do wdrożenia.

---
*Dokument pełniący rolę żywego logu. Zaznaczaj zadania znakiem `[x]` w miarę postępów w pracy.*