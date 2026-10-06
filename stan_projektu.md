# 📓 Dziennik Pokładowy - Stan Projektu: Hybrydowy Portal Zwrotów

## 🎯 Cel główny
Stworzenie asynchronicznego, produkcyjnego systemu do obsługi zwrotów w e-commerce. Portal ma na celu automatyzację procesu obsługi zgłoszeń (Slot-Filling, RAG), redukcję kosztów zapytań LLM poprzez przechwytywanie powtarzalnych pytań (Semantic Router) oraz zachowanie kontroli nad nietypowymi zgłoszeniami dzięki modułowi akceptacji przez administratora (Human-In-The-Loop).

---

## 🧭 Decyzje architektoniczne (log ustaleń)

- **thread_id = ticket.id.** Jedna wartość pełni obie role: klucz główny w tabeli `tickets` (PostgreSQL) i identyfikator wątku w `AsyncPostgresSaver` (LangGraph). Świadome uproszczenie przy założeniu relacji 1:1 ticket↔wątek; brak obsługi scenariusza "klient wraca do tego samego zgłoszenia nową rozmową" — odłożone do rozważenia, jeśli pojawi się realna potrzeba.

- **Moment tworzenia ticketu.** Wiersz w `tickets` zakładany jest przy PIERWSZYM trafieniu wiadomości do grafu (Cache Miss w SemRouter lub wykryty wzorzec numeru zamówienia przez precheck), ze statusem `IN_PROGRESS` — nie dopiero po skompletowaniu wszystkich slotów. `ticket_id` (= `thread_id`) zwracany jest do klienta w odpowiedzi na pierwszą wiadomość i pełni rolę ID czatu. Frontend dołącza go do treści każdej kolejnej wiadomości w tej samej rozmowie — trzymany wyłącznie w pamięci widgetu na czas wizyty (zgodnie z zasadą braku trwałej sesji).

- **Czat bez trwałej sesji klienta** (natywny widget na stronie, jednorazowy — trwa w pamięci JS tylko przez czas wizyty, nie między wizytami). Konsekwencja: po zatwierdzeniu przez admina (HITL) system NIGDY nie wraca do okna czatu — finalizacja zawsze idzie e-mailem. `Node: Finalizacja/Mail` to jedyny kanał domknięcia sprawy po `interrupt()`. Wynika z tego, że e-mail klienta jest obowiązkowym slotem zbieranym w `Node: Zbieranie Danych` — bez niego węzeł finalizujący nie ma gdzie wysłać odpowiedzi.

- **Pre-check numeru zamówienia** wykonywany jako osobna, deterministyczna funkcja PRZED `SemRouter` (nie jako warunek wewnątrz niego) — zero kosztu embeddingu/LLM na tym etapie, łatwa testowalność w izolacji (bez mocków ChromaDB/LLM).

- **Deterministyczny dostęp do danych w węzłach grafu (Wariant B).** `NodeVal` woła funkcje z `app/db/` i `app/rag/` bezpośrednio, jako zwykłe funkcje Pythona — NIE przez function calling LLM-a. Model nie decyduje, czy sprawdzić zamówienie w SQL czy regulamin w ChromaDB — to wykonywane jest zawsze, wynik trafia do modelu jako kontekst. Function calling / structured output LLM-a używane jest wyłącznie tam, gdzie potrzebna jest ekstrakcja lub formatowanie (Node: Zbieranie Danych, pole uzasadnienia w mailu), nigdy do decydowania o wykonaniu zapytań. Konsekwencja nazewnicza: katalog na funkcje SQL/RAG nazywa się `app/db/` i `app/rag/` (po źródle danych), NIE `tools/` — ta nazwa jest zarezerwowana semantycznie dla function-calling, którego tu nie stosujemy.

- **Warunki wejścia do `NodeVal` (odpowiedzialność `NodeGather`).** Zanim graf w ogóle przekaże kontrolę do `NodeVal`, muszą być spełnione: (1) numer zamówienia został poprawnie zweryfikowany w bazie (istnieje rekord w `orders` o podanym `order_id`), (2) limit prób podania poprawnego numeru zamówienia nieprzekroczony (próg: 3 nieudane próby, zob. niżej). To NIE są warunki merytoryczne dotyczące samego zwrotu — to warunki graniczne pętli zbierania danych, decydujące wyłącznie o tym, czy rozmowa ma prawo przejść dalej. `NodeVal` zakłada ich spełnienie i nie weryfikuje ich ponownie — jeśli graf dotarł do `NodeVal`, oba te warunki są już z definicji prawdziwe.

- **Warunki automatycznej kwalifikacji zwrotu (odpowiedzialność `NodeVal`).** Zgłoszenie kwalifikuje się do automatycznej realizacji (pomija HITL) tylko gdy łącznie spełnione są WSZYSTKIE poniższe warunki merytoryczne: (1) zgłoszenie zostało utworzone nie później niż 14 dni od dnia dostarczenia produktu, (2) kwota zwrotu ma wartość <= 600 zł, (3) powód zwrotu znajduje się na zamkniętej liście dozwolonych powodów standardowych zgodnej z §3 regulaminu, (4) status przesyłki to `DELIVERED`. Niespełnienie któregokolwiek z tych czterech warunków → zapis statusu `PENDING` → `interrupt()` → HITL. To jest właściwa logika biznesowa zwrotu, niezależna od tego, jak dane zostały zebrane.

- **Limit prób podania poprawnego numeru zamówienia.** Licznik `order_id_attempts` w stanie grafu, inkrementowany deterministycznie w kodzie (nie przez LLM) po każdej nieudanej weryfikacji w SQL. Po przekroczeniu progu (proponowana wartość startowa: **3 nieudane próby**) sprawa trafia do HITL, ale dopiero po zebraniu e-maila (zob. "Eskalacja HITL bez skompletowanego e-maila"). LLM odpowiada wyłącznie za konwersacyjną prośbę o korektę i ekstrakcję nowej wartości — decyzja o eskalacji to zwykły warunek w kodzie (`if order_id_attempts >= próg`), nie "rozumowanie" modelu. To NIE jest mechanizm agentowy, mimo że z perspektywy rozmowy wygląda na dynamiczny — to licznik i próg jak w każdej innej regule kwalifikacji.

- **Szablon e-maila finalizującego** ma być statyczny (stała struktura), LLM wypełnia wyłącznie pole z uzasadnieniem decyzji — nie generuje całej wiadomości od zera.

- **Endpoint admina do listowania zgłoszeń.** `GET /admin/tickets` zwraca zgłoszenia (domyślnie status `PENDING`, docelowo z możliwością filtrowania po statusie — zob. niżej `FAILED_DELIVERY`) — panel admina nigdy nie łączy się z bazą bezpośrednio, zawsze przez API (poprawione też na diagramie w README.md, gdzie wcześniej sugerował bezpośredni odczyt bazy).

- **Logi aplikacji — brak folderu `logs/` w repozytorium.** Zgodnie z zasadą traktowania logów jako strumienia zdarzeń (12-factor app), aplikacja loguje na stdout/stderr — infrastruktura (Docker, docelowo system agregacji typu Loki/CloudWatch) odpowiada za ich zbieranie. Struktura logu: poziom, timestamp, `thread_id` (gdzie dotyczy), kontekst operacji. Konfiguracja loggera (`app/logging_config.py`) odłożona do momentu realnej potrzeby (Faza 3/4), nie tworzona "na zapas" w Fazie 1.

- **Strategia obsługi błędów zewnętrznych zależności.** Postgres/ChromaDB: retry (kilka prób, krótkie odczekiwanie) wyłącznie w fazie startu aplikacji (`lifespan`) — zabezpieczenie przed wyścigiem startowym kontenerów Docker Compose. Poza startem (w trakcie obsługi żądań): fail-fast, natychmiastowy log z kontekstem (`thread_id`, operacja, wyjątek), bez automatycznego ponawiania — traktowane jako awaria systemowa wymagająca interwencji. API LLM: automatyczny retry z exponential backoff, max 3 próby w budżecie czasowym ~8-10s, stosowany WYŁĄCZNIE dla enumerowanej listy kodów błędów przejściowych (429, 5xx, timeout sieciowy); pozostałe kody (400/401/403/422 i inne błędy trwałe) idą od razu do fail-fast, bez próby ponawiania. Wspólna logika retry (np. przez bibliotekę `tenacity`) używana jednym mechanizmem we wszystkich wywołaniach LLM w projekcie, nie duplikowana per węzeł.

- **Idempotencja wysyłki e-maila (`Node: Finalizacja/Mail`).** Wywołanie LLM po tekst uzasadnienia jest bezstanowe i bezpieczne do retry. Faktyczna wysyłka e-maila NIE jest bezpiecznie retry'owalna wprost (ryzyko podwójnej wysyłki tej samej decyzji do klienta) — przed wysyłką węzeł sprawdza aktualny status ticketu w Postgresie; jeśli już `RESOLVED`, pomija ponowną wysyłkę. Status aktualizowany na `RESOLVED` dopiero PO potwierdzonym sukcesie wysyłki, nigdy przed.

- **Trwała awaria wysyłki po zatwierdzeniu przez admina.** Jeśli generowanie uzasadnienia lub wysyłka maila ostatecznie zawiedzie mimo wyczerpania prób retry, ticket NIE wraca automatycznie do kolejki `PENDING` (myliłoby to admina, sugerując nowe zgłoszenie do oceny, i mogłoby tworzyć pętlę przy trwałej awarii). Zamiast tego przyjmuje osobny status `FAILED_DELIVERY`, wymagający ręcznego ponowienia po stronie admina/operatora po ustaniu przyczyny awarii infrastruktury (np. dostawca poczty wrócił do działania).

- **Ekstrakcja oportunistyczna slotów w `Node: Zbieranie Danych`.** Węzeł nie pyta o sloty sekwencyjnie (numer zamówienia → e-mail → powód), tylko przy KAŻDYM wywołaniu próbuje wyciągnąć z bieżącej wiadomości klienta wszystkie trzy sloty naraz (`order_id`, `email`, `reason`), niezależnie od tego, o który slot ostatnio dopytywał bot. Wynika to z realnego scenariusza konwersacyjnego: klient może w pierwszej wiadomości podać komplet informacji naraz ("zwrot zamówienia #4471, rozmiar był za mały, mój mail to..."), a sekwencyjne pytanie o dane już podane byłoby złym UX i sprawiałoby wrażenie, że bot nie czyta wiadomości klienta.

  Schemat ekstrakcji Pydantic ma wszystkie trzy pola jako `Optional` — LLM zwraca `None` dla slotu, którego nie da się wyciągnąć z danej wiadomości, zamiast go halucynować. Po otrzymaniu wyniku ekstrakcji kod (nie LLM) scala nowe wartości ze stanem grafu: nadpisuje WYŁĄCZNIE te pola stanu, które są jeszcze puste. Dopiero po scaleniu węzeł deterministycznie sprawdza, których slotów wciąż brakuje, i formułuje pytanie do klienta tylko o nie — jeśli brakuje wyłącznie `reason`, bot dopytuje wyłącznie o powód (prezentując tekstowo 15 pozycji z §3 regulaminu + opcję "inne: podaj powód"), pomijając pytania o sloty już zebrane.

- **`order_id` jest polem "zamrożonym" po pozytywnej weryfikacji w bazie** — jeśli klient w kolejnej wiadomości poda inny numer zamówienia niż ten już zweryfikowany, węzeł go ignoruje (nie nadpisuje stanu), traktując pierwszy zweryfikowany numer jako wiążący dla całej rozmowy. Zapobiega to sytuacji, w której klient mógłby manipulować przebiegiem rozmowy, podając na przemian różne numery zamówień w trakcie tej samej sesji.

- **Sterownik Postgresa: psycopg 3 wszędzie (wariant A).** Checkpointer LangGraph wymaga psycopg 3, więc dokładanie asyncpg dawałoby dwa API i dwa dialekty parametrów. Koszt zmiany był zerowy (`app/db/` nie zawierało kodu). Odrzucony wariant B: asyncpg dla logiki biznesowej + psycopg dla checkpointera. Szczegóły w README, sekcja "Decyzje architektoniczne".

- **Dwie pule, jeden sterownik.** Pula biznesowa (`AsyncConnectionPool`, lifespan FastAPI) i osobna pula checkpointera (autocommit, `dict_row`), żeby jego konfiguracja nie wpływała na transakcje zapytań biznesowych. Budżet `max_connections` Postgresa to suma obu pul.

- **Migracje: Alembic bez ORM.** `upgrade()` / `downgrade()` piszemy ręcznie z surowym SQL-em, tylko dla tabel biznesowych. Tabele checkpointera zostają poza migracjami. Pierwsza migracja zastępuje `create_table.sql`, a enumy tworzy w `CREATE TYPE` (migracja buduje schemat od zera).

- **Reguły w bazie i w Pydantic.** `orders.delivered_at` z CHECK (`DELIVERED` wymaga daty); `tickets` z CHECK: `return_status = 'IN_PROGRESS'` albo `contact_email IS NOT NULL`; `return_status` i `shipped_status` są NOT NULL. Pydantic chroni wejście do aplikacji, baza chroni każdą ścieżkę zapisu (psql, skrypty, przyszły kod). Predykat `IS NOT NULL` zamiast `<> ''`, bo porównanie z NULL przechodzi przez CHECK.

- **Eskalacja HITL bez skompletowanego e-maila.** Ticket istnieje od pierwszej wiadomości (`IN_PROGRESS`). Po przekroczeniu progu prób, gdy e-mail jest pusty, `NodeGather` dopytuje o adres i dopiero po jego otrzymaniu zmienia status na `PENDING`. Nie tworzy nowego ticketu.

- **Odpowiedź niebędąca e-mailem.** Slot zostaje pusty, ticket w `IN_PROGRESS`, pytanie się powtarza, a bot mówi wprost, że bez adresu zgłoszenie nie zostanie przekazane dalej. Klient, który nigdy nie poda adresu, trafia do Known Limitations (bez osobnego licznika).

- **HITL na `interrupt()` zamiast `interrupt_before`.** `NodeHITL` zawiera wyłącznie pauzę, a zapis `PENDING` wykonuje się w `NodeVal` przed nią, bo po wznowieniu węzeł startuje od początku. Wznowienie przez `Command(resume=...)`, nie `graph.update_state()`.

- **`thread_id` jako tekst.** Kolumna `thread_id` w tabelach checkpointera to `TEXT`, więc `thread_id = str(ticket.id)` przez jeden helper.

- **Uruchamianie na Windowsie.** psycopg async wymaga `SelectorEventLoop`: `uvicorn app.main:app --reload --loop asyncio:SelectorEventLoop` (do przetestowania z `--reload`).


---

## 📝 Lista zadań (Checklista)

### 🛠️ Faza 1: Konfiguracja i Środowisko
- [x] Inicjalizacja repozytorium i struktury projektu (Python 3.12+).
- [x] Utworzenie szkieletu katalogów zgodnie z ustaloną strukturą: `app/`, `app/db/`, `app/rag/`, `app/graph/`, `app/email/`, `data/`, `scripts/`, `tests/` (zob. README.md, sekcja "Struktura Projektu").
- [x] Konfiguracja narzędzi do kontroli jakości kodu (Ruff, Mypy) w `pyproject.toml`.
- [x] Przygotowanie pliku `docker-compose.yml` uruchamiającego TYLKO usługi stanowe (PostgreSQL 16, ChromaDB) do lokalnego developmentu — aplikacja FastAPI uruchamiana lokalnie przez `uvicorn --reload`, bez kontenera na tym etapie.

### 🗄️ Faza 2: Bazy Danych i Persystencja
- [ ] Utworzenie schematów i migracji dla relacyjnej bazy PostgreSQL (Alembic, tabele biznesowe `orders`, `tickets`). Definicja "zrobione": pierwsza migracja odtwarzalna od zera jedną komendą, zastępuje `scripts/create_table.sql`.
  - [ ] Inicjalizacja Alembica (folder `migrations/`, `env.py` z adresem `postgresql+psycopg://`, tabele checkpointera wykluczone z migracji).
  - [ ] W `orders`: `shipped_status NOT NULL DEFAULT 'UNSENT'`, `delivered_at` (TIMESTAMPTZ) z CHECK: `DELIVERED` wymaga daty.
  - [ ] W `tickets`: kolumna identyfikatora pełni podwójną rolę jako `thread_id` (zob. Decyzje architektoniczne). `return_status NOT NULL`, kolumna e-maila nullable z CHECK (poza `IN_PROGRESS` e-mail wymagany, wymagany do finalizacji po HITL). Statusy: `IN_PROGRESS`, `PENDING`, `PROCESSING_RESOLUTION`, `RESOLVED`, `FAILED_DELIVERY` (zob. Decyzje architektoniczne — trwała awaria wysyłki).
  - [ ] Test migracji: czysta baza → `upgrade` → `downgrade` → `upgrade`.
  - [ ] Seed danych testowych dla `orders` (dane syntetyczne, skrypt w `scripts/`).
- [ ] Wdrożenie wektorowej bazy danych ChromaDB (kontener w `docker-compose.yml` jest; do zrobienia: połączenie z aplikacji i weryfikacja).
- [ ] Utworzenie i zasilenie kolekcji ChromaDB:
  - [x] Źródła w `data/` gotowe: `static_intents.yaml` (intencje dla Semantic Routera), `regulamin.md` (RAG).
  - [ ] Kolekcja `static_intents` w ChromaDB (loader wczytujący `static_intents.yaml`).
  - [ ] Kolekcja `return_policy` w ChromaDB (skrypt `scripts/ingest_policy.py` czytający `data/regulamin.md`, na potrzeby RAG).
- [ ] Integracja checkpointera `AsyncPostgresSaver` (psycopg 3, osobna pula) dla zachowywania zserializowanych stanów grafu (LangGraph).

### 🌐 Faza 3: Warstwa API (Backend)
- [ ] Uruchomienie szkieletu aplikacji FastAPI (uruchamianej lokalnie przez `uvicorn --reload`).
- [ ] Uruchomienie lokalne na Windowsie z `--loop asyncio:SelectorEventLoop` (także z `--reload`).
- [ ] Konfiguracja puli połączeń psycopg (`AsyncConnectionPool`) podpiętej pod cykl życia aplikacji (lifespan) FastAPI oraz osobnej puli dla checkpointera — z krótkim retry na wypadek wyścigu startowego kontenerów (zob. Decyzje architektoniczne).
- [ ] Przygotowanie modeli w Pydantic v2 (`app/schemas.py`) do rygorystycznej walidacji (format `order_id`, format e-maila, parsowanie JSON) — reużywanych jako schemat ekstrakcji danych dla LLM w węźle Zbieranie Danych.
- [ ] Decyzja: walidacja e-maila przez `EmailStr` (wymaga `email-validator` w `pyproject.toml`) czy regex — rozstrzygnąć przy `app/schemas.py`.
- [ ] Implementacja głównego endpointu dla klientów: `POST /chat`. Kontrakt: opcjonalne pole `ticket_id` w request body — brak = pierwsza wiadomość (tworzy ticket), obecność = kontynuacja rozmowy.
- [ ] Implementacja endpointu dla administratora do listowania zgłoszeń: `GET /admin/tickets` (domyślnie status `PENDING`, z możliwością filtrowania — w tym po `FAILED_DELIVERY`).
- [ ] Implementacja endpointu dla administratora do zwalniania blokad (HITL): `POST /admin/tickets/{id}/resolve`.
- [ ] Implementacja endpointu do ręcznego ponowienia wysyłki maila dla ticketów ze statusem `FAILED_DELIVERY`: `POST /admin/tickets/{id}/retry-mail`.
- [ ] (Opcjonalnie, w razie potrzeby) Podstawowa konfiguracja loggera (`logging`) pisząca do stdout — NIE do pliku/folderu w repo (zob. Decyzje architektoniczne).

### 🧠 Faza 4: Inteligencja, Routing i Orkiestracja
- [ ] **Skonfigurować GitHub Actions (CI) — odroczone świadomie do tego momentu.** Uruchamianie `ruff check`, `mypy` i `pytest` przy każdym pushu/PR. Celowo NIE zrobione wcześniej (Faza 2-3) — bez realnego kodu aplikacyjnego i testów w `tests/`, CI weryfikowałoby pusty projekt, dając fałszywe poczucie bezpieczeństwa bez faktycznej ochrony. Ma sens dopiero od momentu, gdy istnieje pierwszy testowalny fragment logiki (start Fazy 4). Workflow musi jawnie używać właściwej grupy zależności (`[dependency-groups]`, zob. sekcja zarządzania środowiskiem) — nie gołego `uv sync` bez flag, inaczej `ruff`/`mypy`/`pytest` nie będą fizycznie obecne w środowisku CI.
- [x] ⚠️ **Uzupełnić i spisać pełną listę dozwolonych "standardowych" powodów zwrotu** — wymagane przed implementacją reguł kwalifikacji poniżej.
- [ ] **Pre-check numeru zamówienia (przed routerem):**
  - [ ] Implementacja deterministycznej funkcji wykrywającej wzorzec numeru zamówienia w treści wiadomości klienta.
  - [ ] Rozdzielenie ścieżek: wzorzec obecny → pominięcie `SemRouter`, bezpośrednio do `Graph`; wzorzec nieobecny → standardowa ścieżka przez `SemRouter`.
  - [ ] **Test jednostkowy dla funkcji pre-check** (przeniesiony z Fazy 5) — izolowany, bez mocków ChromaDB/LLM, pisany od razu przy implementacji, nie po fakcie.
- [ ] **Semantic Router:**
  - [ ] Konfiguracja modelu embeddingów (np. BGE).
  - [ ] Implementacja mechanizmu wyliczania dystansu (Match < 0.25 -> Cache Hit, w przeciwnym razie Cache Miss).
- [ ] **Warstwa dostępu do danych (`app/db/`, `app/rag/`):**
  - [ ] Funkcje SQL: tworzenie ticketu, aktualizacja statusu (w tym `FAILED_DELIVERY`), weryfikacja zamówienia, listowanie ticketów wg statusu.
  - [ ] Funkcje RAG: bezpośrednie wyszukiwanie w kolekcji `return_policy`.
  - [ ] Jawnie zaprojektowana obsługa wyjątków przy każdej funkcji (zob. Decyzje architektoniczne — strategia fail-fast/retry).
- [ ] **LangGraph App (Maszyna Stanów):**
  - [ ] Definicja stanu grafu (`app/graph/state.py`) — w tym licznik `order_id_attempts`.
  - [ ] Budowa węzła: `Node: Zbieranie Danych` (Slot-Filling z użyciem Function Calling/structured output). Obowiązkowe sloty: min. numer zamówienia, e-mail klienta, powód zwrotu. Ekstrakcja oportunistyczna — przy błędnym numerze zamówienia: konwersacyjna prośba o korektę + inkrementacja licznika prób.
    - [ ] Eskalacja bez e-maila: po przekroczeniu progu, gdy e-mail pusty, jednorazowe dopytanie zanim status zmieni się na `PENDING`. Odpowiedź niebędąca e-mailem: pytanie się powtarza, ticket zostaje w `IN_PROGRESS`.
  - [ ] Budowa węzła: `Node: Walidacja & RAG` — deterministyczne (nie agentowe) wywołania funkcji z `app/db/` i `app/rag/`.
    - [ ] Implementacja warunków automatycznej kwalifikacji zwrotu (zob. Decyzje architektoniczne): czas od dostarczenia (`orders.delivered_at`), próg kwotowy, lista powodów, status przesyłki (`DELIVERED`).
    - [ ] **Test jednostkowy dla logiki reguł kwalifikacji** (14 dni / 600 zł / lista powodów / status przesyłki) — izolowany, sam moduł Python, bez sieci, pisany równolegle z implementacją, nie po zamknięciu całej Fazy 4.
  - [ ] Budowa węzła pauzy: `NodeHITL` zawierający wyłącznie `interrupt()` (wstrzymanie grafu, gdy którakolwiek reguła kwalifikacji nie jest spełniona). Zapis statusu `PENDING` w `NodeVal` przed pauzą.
  - [ ] Budowa węzła: `Node: Finalizacja/Mail` (Generowanie podsumowania/komunikatu do klienta, wysyłka e-mailem).
    - [ ] Zaprojektowanie statycznego szablonu maila (stała struktura + jedno zmienne pole na uzasadnienie decyzji generowane przez LLM).
    - [ ] Implementacja sprawdzenia statusu przed wysyłką (idempotencja) + aktualizacja na `RESOLVED` dopiero po potwierdzonym sukcesie (zob. Decyzje architektoniczne).
    - [ ] **Test jednostkowy dla logiki idempotencji wysyłki maila** (przeniesiony z Fazy 5) — mock statusu ticketu, weryfikacja że wysyłka jest pomijana przy `RESOLVED`.
    - [ ] Obsługa trwałej awarii wysyłki: ustawienie statusu `FAILED_DELIVERY` po wyczerpaniu prób retry.
  - [ ] Start: wszystkie 4 węzły w jednym pliku `app/graph/nodes.py` — rozbicie na osobne pliki tylko jeśli złożoność (zwłaszcza `Walidacja & RAG`) wyraźnie urośnie.
  - [ ] Budowa i kompilacja grafu (`app/graph/builder.py`) z checkpointerem.
- [ ] Integracja z zewnętrznym silnikiem LLM (OpenAI / Anthropic / Gemini).
  - [ ] **Przygotowanie mocków dla zewnętrznego API LLM** (przeniesiony z Fazy 5) — testowanie niezależne od sieci, w tym scenariuszy błędów retry-worthy vs fail-fast.
- [ ] Implementacja wspólnego mechanizmu retry (exponential backoff, enumerowana lista kodów błędów: 429/5xx/timeout → retry, 400/401/403/422 → fail-fast) dla wszystkich wywołań LLM w projekcie — jedna funkcja pomocnicza, nie duplikowana per węzeł.
- [ ] Opracowanie mechaniki wznawiania działania grafu (`Command(resume=...)`, zamiast `graph.update_state()`) po obsłudze zdarzenia przez admina.
  - [ ] **Weryfikacja stanu bazy danych i poprawności przepływów (flow) maszyny LangGraph** (przeniesiony z Fazy 5) — test integracyjny end-to-end całej ścieżki: zbieranie → walidacja → HITL → wznowienie → finalizacja.

### 🧪 Faza 5: Testy i Utrzymanie (DevOps & CI/CD)
- [x] Konfiguracja środowiska testowego asynchronicznego (`pytest`, `pytest-asyncio`, `pyproject.toml`: `asyncio_mode = auto`, `testpaths`, grupa `dev`).
- [ ] Zaimplementowanie testów dla endpointów API.
- [ ] Przegląd pokrycia testami przed zamknięciem projektu (testy jednostkowe i flow są pisane w Fazie 4 równolegle z kodem).
- [ ] Przygotowanie docelowego `Dockerfile` dla aplikacji FastAPI (ewentualnie multi-stage build) gotowego do wdrożenia.

---
*Dokument pełniący rolę żywego logu. Zaznaczaj zadania znakiem `[x]` w miarę postępów w pracy.*