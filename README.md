###### Pydantic rules 

- always create a base for schema followed by 
reqeuest,response,update,deleted 

reference  schemas/payment.py

###### GIT RULES 

1. Feature Development
   ├─ git checkout develop
   ├─ git pull origin develop
   ├─ git checkout -b feature/add-new-cart
   ├─ (work, commit, push)
   └─ Create PR: feature/add-new-cart → develop
2. Merge to Develop (after PR approved)
   ├─ Merge PR
   ├─ git checkout develop
   ├─ git pull origin develop
   └─ Auto-deploy to STAGING environment
3. Test on Staging
   ├─ QA tests the feature
   ├─ If bugs found → create bugfix branch from develop
   └─ If all good → ready for production!
4. Release to Production (Main)
   ├─ git checkout main
   ├─ git pull origin main
   ├─ git merge develop
   ├─ git tag -a v0.2.0 -m "Release v0.2.0 - Added new cart"
   ├─ git push origin main
   ├─ git push origin v0.2.0
   └─ Deploy v0.2.0 tag to PRODUCTION

###### Clearing cache

rm -rf node_modules
rm -f package-lock.json
npm cache clean --force
npm install

###### Notes 

- always right clean code
- delete weird comments 
- right ready to test code







