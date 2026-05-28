from version import version
from updatecheck import *
from mixer.app import main
from first_run import check_pipewire
if __name__ == "__main__":
    #updatecheck()
    #print(version)
    check_pipewire()
    main()
