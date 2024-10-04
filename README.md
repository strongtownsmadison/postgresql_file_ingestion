# postgresql_file_ingestion
Contains scripts that control ingestion of various files into a staging schema on our PostgreSQL server.

I'm trying to build a more generic utility to interface with the PostgreSQL server, which lives in the utils folder. This can be expanded to be other common things across ingestion of different sources.

Right now this only contains ingestion for tax roll xlsx files.

FYI, I haven't done really robust python development before, so a lot of concepts are new to me. I'm using Claude/ChatGPT to fill gaps and learn, so feel free to point out anything ridiculous I'm doing.

If you want to talk about anything, you can @Ben Noffke on the Madison Strong Towns Discord.

## Set up to run locally
I need to really build this out/script a way to make it easier for people.

Prereqs:
- Some sort of linux install
- Local PostgreSQL database
  - You'll want to create a user for yourself and grant them a lot of permissions.
  - You'll need to create a staging schema: ```create schema if not exists staging```
- Run ```./setup_env.sh```
- A .env.dev file in the project root, something like this:
  - ```
    DB_HOST=localhost
    DB_NAME=local_db
    DB_USER=bnoffke
    DB_PASSWORD=dev

    SOURCE_DIR=/home/bnoffke/data/tax_roll_xlsx/
    ```
  - To-do: The SOURCE_DIR needs to be more specific to the ingestion task
- Activate the environment:
  - ```source ./venv/bin/activate```
- You can run the tax roll ingestion with
  - ```python3 -m src.tax_roll_excel_ingest.tax_roll_excel_ingest```
  - This will look for tax roll xlsx files in ```SOURCE_DIR```.

## Some other to-dos
- Get the utils stuff working as a package
  - Works as module call (```python), not as console script
- Get GitHub secrets for prod connection
- Containerize
  - Lots to figure out here
- How to correctly point to SQL scripts, current approach is kind of fine.
- Other best practices I'm not thinking about 
