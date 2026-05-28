from version import version
from updatecheck import *
from mixer.app import main
from first_run import check_dependencies
if __name__ == "__main__":
    check_dependencies()
    main()
