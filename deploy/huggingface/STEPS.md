# Publishing the demo on a Hugging Face Space, step by step

Optional. A Space runs the same Docker image as `docker run`, in sample mode: no key, no secret,
no model call. Everything below is done by the account owner; nothing here creates an account,
logs in or pushes on anyone's behalf.

## Once

1. Create a Hugging Face account at https://huggingface.co/join (the owner does it, with their own
   e-mail and password).
2. Create an access token with **write** permission: Settings, Access Tokens, New token. Keep it in a
   password manager; never paste it in a file of this repository, a commit, a trace or a video.
3. Install Git LFS: https://git-lfs.com (Windows: it ships with Git for Windows; run `git lfs install` once).

## Create the Space

4. https://huggingface.co/new-space: owner = your account, name `greentrace`, licence Apache-2.0,
   SDK **Docker**, template **Blank**, hardware **CPU basic** (free), visibility Public.
5. Do **not** add any secret or variable: sample mode needs none, and `SAMPLE_MODE=true` is already the
   image's default.

## Fill it and push (Git Bash, from the greentrace folder)

```bash
git pull                                                     # the version you want to publish
git clone https://huggingface.co/spaces/<you>/greentrace ../greentrace-space
sh deploy/huggingface/prepare_space.sh ../greentrace-space
cd ../greentrace-space
git lfs install
git add .
git commit -m "GreenTrace, sample mode"
git push                                                     # user: <you>, password: the write token
```

6. Open `https://huggingface.co/spaces/<you>/greentrace`: the build log shows the three Docker stages
   (a few minutes the first time), then the app starts on port 8000 (`app_port` in the Space card).
7. Check: the home page loads, "Run the demo case" gives CONTRADICTED, and `/health` answers `{"status":"ok"}`.

## Afterwards

- To update: `git pull` here, run `prepare_space.sh` again, then commit and push in the Space clone.
- The Space sleeps when nobody uses it; the first visit after that waits for it to wake up.
- H-MEM notes and logs live in the container and are reset when the Space restarts.
- Put the Space URL in `docs/SUBMISSION.md` (Live demo) only once step 7 passes.
